# -*- coding: utf-8 -*-
"""真的去跑一輪：claude -p / codex exec / OpenAI 相容 API，stream 事件塞進 run 的事件流。"""
import asyncio
import json
import os
import subprocess
import uuid

from .bridge import ASK_MCP_CONFIG, ASK_MCP_READY, ASK_SYSTEM_PROMPT, PERM_READY, PERM_SETTINGS
from .config import API_CHATS, CLAUDE_ARGV, CODEX_EXE, CONFIG, ENGINES, PERMISSION_FLAGS, PORT, load_keys, log
from .desktop import desktop_open
from .jsonl import _ctx_window, _loads, items_from_message
from .rooms import remember_app_sid
from .runs import BY_SESSION, _emit, _gc_run

# ---------- 執行 claude ----------

async def run_codex(run, text, mode):
    """Codex：codex exec --json（新對話）或 codex exec resume <id>（續聊）。"""
    argv = [CODEX_EXE, "exec", "--json", "--skip-git-repo-check", "-C", run.cwd]
    argv += ["-s", "danger-full-access" if mode == "auto" else "read-only"]
    if mode == "auto":
        argv.append("--dangerously-bypass-approvals-and-sandbox")
    if run.sid:
        argv += ["resume", run.sid, "-"]
    else:
        argv.append("-")
    stderr_tail = ""
    done_sent = False
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=run.cwd, limit=16 * 1024 * 1024,
            creationflags=subprocess.CREATE_NO_WINDOW)
        run.proc = proc
        proc.stdin.write(text.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        async def rerr():
            nonlocal stderr_tail
            stderr_tail = (await proc.stderr.read()).decode("utf-8", "replace")[-1500:]
        err_task = asyncio.create_task(rerr())

        win = 0
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            ev = _loads(line.decode("utf-8", "replace"))
            if not ev:
                continue
            et = ev.get("type")
            if et == "thread.started":
                if not run.sid and ev.get("thread_id"):
                    run.sid = ev["thread_id"]
                    BY_SESSION[run.sid] = run.id
                await _emit(run, {"kind": "init", "sid": run.sid})
            elif et == "item.completed":
                item = ev.get("item") or {}
                it = item.get("type")
                if it == "agent_message" and (item.get("text") or "").strip():
                    await _emit(run, {"role": "assistant", "kind": "text", "text": item["text"]})
                elif it == "command_execution":
                    await _emit(run, {"role": "assistant", "kind": "tool", "tool": "Shell",
                                      "detail": str(item.get("command", ""))[:160],
                                      "ok": item.get("exit_code", 0) == 0})
                elif it in ("file_change", "patch_apply"):
                    await _emit(run, {"role": "assistant", "kind": "tool", "tool": "改檔案",
                                      "detail": str(item.get("path", ""))[:160], "ok": True})
                elif it == "error" and item.get("message"):
                    log.info("codex note: %s", item["message"][:200])
            elif et == "turn.started":
                win = ev.get("model_context_window") or win
            elif et == "turn.completed":
                u = ev.get("usage") or {}
                tok = (u.get("input_tokens") or 0) + (u.get("cached_input_tokens") or 0)
                if tok:
                    w = win or 258400
                    await _emit(run, {"kind": "ctx", "tokens": tok, "window": w,
                                      "pct": round(tok * 100 / w)})
                await _emit(run, {"kind": "done", "ok": True, "sid": run.sid, "error": ""})
                done_sent = True
            elif et == "turn.failed":
                err = ((ev.get("error") or {}).get("message") or "Codex 這一輪失敗")[:400]
                await _emit(run, {"kind": "done", "ok": False, "sid": run.sid, "error": err})
                done_sent = True
        await proc.wait()
        await err_task
        if not done_sent:
            msg = stderr_tail.strip() or f"Codex 結束但沒有回覆（exit {proc.returncode}）"
            await _emit(run, {"kind": "done", "ok": False, "sid": run.sid, "error": msg[-400:]})
    except Exception as e:
        log.exception("run_codex failed")
        await _emit(run, {"kind": "done", "ok": False, "sid": run.sid,
                          "error": f"{type(e).__name__}: {e}"})
    finally:
        if run.sid and BY_SESSION.get(run.sid) == run.id:
            BY_SESSION.pop(run.sid, None)
        asyncio.get_event_loop().create_task(_gc_run(run.id))


def _api_append(eng, sid, role, content, model=None):
    d = API_CHATS / eng
    d.mkdir(parents=True, exist_ok=True)
    rec = {"role": role, "content": content,
           "ts": __import__("datetime").datetime.now().astimezone().isoformat()}
    if model:
        rec["model"] = model
    with open(d / (sid + ".jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


async def run_api(run, text, engine, model):
    """OpenAI 相容的 API 引擎（Grok/Gemini/ChatGPT/DeepSeek/OpenRouter），串流回覆。"""
    import urllib.error
    import urllib.request
    spec = ENGINES[engine]
    key = load_keys().get(engine, {}).get("key", "")
    model = model or load_keys().get(engine, {}).get("model") or spec["model"]
    if not key:
        await _emit(run, {"kind": "done", "ok": False, "sid": run.sid,
                          "error": "還沒設定 " + spec["label"] + " 的 API key（設定 → 其他 AI）"})
        return
    if not run.sid:
        run.sid = uuid.uuid4().hex[:16]
        BY_SESSION[run.sid] = run.id
        await _emit(run, {"kind": "init", "sid": run.sid})

    msgs = []
    f = API_CHATS / engine / (run.sid + ".jsonl")
    if f.exists():
        for line in f.read_bytes().decode("utf-8", "replace").splitlines():
            r = _loads(line)
            if r and r.get("content"):
                msgs.append({"role": r.get("role", "user"), "content": r["content"]})
    msgs = msgs[-30:] + [{"role": "user", "content": text}]
    _api_append(engine, run.sid, "user", text, model)

    body = json.dumps({"model": model, "messages": msgs, "stream": True}).encode()
    req = urllib.request.Request(
        spec["base"].rstrip("/") + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})

    acc = []
    loop = asyncio.get_event_loop()
    try:
        def pump():
            out = []
            with urllib.request.urlopen(req, timeout=180) as resp:
                for raw in resp:
                    s = raw.decode("utf-8", "replace").strip()
                    if not s.startswith("data:"):
                        continue
                    payload = s[5:].strip()
                    if payload == "[DONE]":
                        break
                    d = _loads(payload)
                    if not d:
                        continue
                    for ch in d.get("choices") or []:
                        piece = (ch.get("delta") or {}).get("content")
                        if piece:
                            out.append(piece)
            return "".join(out)

        full = await loop.run_in_executor(None, pump)
        acc.append(full)
        if full.strip():
            await _emit(run, {"role": "assistant", "kind": "text", "text": full})
            _api_append(engine, run.sid, "assistant", full, model)
        await _emit(run, {"kind": "done", "ok": True, "sid": run.sid, "error": ""})
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        msg = ("API key 被拒絕，檢查一下是不是貼錯或過期" if e.code in (401, 403)
               else f"HTTP {e.code}：{detail}")
        await _emit(run, {"kind": "done", "ok": False, "sid": run.sid, "error": msg})
    except Exception as e:
        await _emit(run, {"kind": "done", "ok": False, "sid": run.sid,
                          "error": f"{type(e).__name__}: {e}"})
    finally:
        if run.sid and BY_SESSION.get(run.sid) == run.id:
            BY_SESSION.pop(run.sid, None)
        asyncio.get_event_loop().create_task(_gc_run(run.id))


async def run_claude(run, text, mode, extra_args=()):
    argv = CLAUDE_ARGV + ["-p", "--output-format", "stream-json", "--verbose"]
    # 送訊息的入口已經擋過未知模式，這裡的預設值只是雙保險：退到最安全的那個
    argv += PERMISSION_FLAGS.get(mode, PERMISSION_FLAGS["plan"])
    if ASK_MCP_READY:
        # 提問通道要在任何權限模式下都免詢問（-p 沒有 UI，詢問等於直接被拒）
        argv += [
            "--mcp-config", str(ASK_MCP_CONFIG),
            "--allowedTools", "mcp__chat__ask_user",
            "--append-system-prompt", ASK_SYSTEM_PROMPT,
        ]
    if mode == "ask" and PERM_READY:
        # 逐項授權：default 權限模式 + 手機橋接 hook（沒有 hook 的話 -p 會把每個工具直接拒掉）
        argv += ["--settings", str(PERM_SETTINGS)]
    argv += list(extra_args)
    if run.sid:
        argv += ["--resume", run.sid]
    stderr_tail = ""
    got_done = False
    try:
        # 讓 AskUserQuestion 橋接 hook 認得這是本 App 開的 run（hook 沒讀到這兩個
        # 變數會直接放行，桌面與一般 CLI 完全不受影響）
        child_env = dict(os.environ)
        child_env["CLAUDE_CHAT_RUN_ID"] = run.id
        child_env["CLAUDE_CHAT_PORT"] = str(PORT)
        # ask_user 要等真人在手機上點選，工具呼叫逾時放寬到 10 分鐘
        child_env["MCP_TOOL_TIMEOUT"] = "600000"
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=run.cwd,
            env=child_env,
            limit=16 * 1024 * 1024,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        run.proc = proc
        proc.stdin.write(text.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        async def read_stderr():
            nonlocal stderr_tail
            data = await proc.stderr.read()
            stderr_tail = data.decode("utf-8", "replace")[-2000:]

        err_task = asyncio.create_task(read_stderr())

        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            ev = _loads(line.decode("utf-8", "replace"))
            if not ev:
                continue
            et = ev.get("type")
            if et == "system" and ev.get("subtype") == "init":
                if not run.sid and ev.get("session_id"):
                    run.sid = ev["session_id"]
                    BY_SESSION[run.sid] = run.id
                # 記下來，不然這場新對話會被 sdk-cli 的過濾規則藏掉
                try:
                    remember_app_sid(run.sid)
                except Exception as e:
                    log.warning("記錄 app session id 失敗：%s", e)
                await _emit(run, {"kind": "init", "sid": run.sid})
            elif et == "assistant":
                msg = ev.get("message") or {}
                for it in items_from_message("assistant", msg):
                    await _emit(run, it)
                u = msg.get("usage")
                if isinstance(u, dict):
                    tok = ((u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                           + (u.get("cache_creation_input_tokens") or 0))
                    if tok > 0:
                        win = _ctx_window(msg.get("model"), tok)
                        await _emit(run, {"kind": "ctx", "tokens": tok, "window": win,
                                          "pct": round(tok * 100 / win)})
            elif et == "user":
                content = (ev.get("message") or {}).get("content")
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            await _emit(run, {"kind": "tool_ok",
                                              "tool_use_id": b.get("tool_use_id"),
                                              "ok": not b.get("is_error", False)})
            elif et == "result":
                got_done = True
                await _emit(run, {
                    "kind": "done",
                    "ok": ev.get("subtype") == "success",
                    "sid": ev.get("session_id") or run.sid,
                    "error": (ev.get("result") or "")[:500] if ev.get("is_error") else "",
                    "duration_ms": ev.get("duration_ms"),
                })
        await proc.wait()
        await err_task
        if not got_done:
            msg = stderr_tail.strip() or f"claude 結束了但沒有回傳結果（exit {proc.returncode}）"
            await _emit(run, {"kind": "done", "ok": False, "sid": run.sid, "error": msg[-500:]})
        elif run.is_new and run.sid and CONFIG["desktop_sync"] == "auto":
            # 手機開的新對話做完第一輪就登錄進桌面 app（桌面 app 會切到這個 session，一次而已）
            try:
                res = await asyncio.to_thread(desktop_open, run.sid)
                log.info("desktop sync %s -> %s", run.sid, res)
            except Exception as e:
                log.warning("desktop sync failed for %s: %s", run.sid, e)
    except Exception as e:
        log.exception("run_claude failed")
        await _emit(run, {"kind": "done", "ok": False, "sid": run.sid,
                          "error": f"{type(e).__name__}: {e}"})
    finally:
        if run.sid and BY_SESSION.get(run.sid) == run.id:
            BY_SESSION.pop(run.sid, None)
        asyncio.get_event_loop().create_task(_gc_run(run.id))
