# -*- coding: utf-8 -*-
"""FastAPI 路由層：所有 /api/*、靜態頁、前端 JS 組裝。這裡只做參數檢查與呼叫其他模組，邏輯不放這裡。"""
import asyncio
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .bgtasks import list_bg_tasks
from .bridge import PERM_READY
from .config import (
    ACTIVE_CONTENT_EXTS,
    ALLOWED_FILE_ROOTS,
    API_SLUG,
    CLAUDE_ARGV,
    CODEX_EXE,
    CONFIG,
    CONFIG_FILE,
    DOC_EXTS,
    EFFORTS,
    ENGINES,
    HOME,
    IMAGE_MAGIC,
    INLINE_OK_EXTS,
    MAX_CONCURRENT_RUNS,
    MODELS,
    PERMISSION_FLAGS,
    PROJECTS_DIR,
    SLUG_ENGINE,
    STATIC,
    TRASH_DIR,
    UPLOAD_CHUNK,
    UPLOAD_DIR,
    UPLOAD_EXTS,
    UPLOAD_MAX,
    UPLOAD_QUOTA,
    load_keys,
    log,
    save_keys,
)
from .desktop import desktop_app_available, desktop_open, desktop_registry
from .history import _room_busy, api_history, codex_history, load_history, tail_items
from .jsonl import _clean_title, _head_info, _tool_detail
from .peer import (
    _peer,
    _peer_register,
    _peer_touch_loop,
    _peer_unregister,
    _PeerProtocol,
    live_peer_for,
    peer_interrupt,
    run_peer,
)
from .perm import PERMS, _perm_card, _perm_key, pending_perms
from .rooms import _codex_info, _room_cache, _safe_name, find_room_file, load_overlay, save_overlay, save_title, scan_rooms
from .runner import run_api, run_claude, run_codex
from .runs import BY_SESSION, RUNS, Run, _emit
from .search import search_rooms
from .usage import plan_limits, token_usage

# ---------- API ----------

app = FastAPI(title="claude-chat")
# 手機常繞 Tailscale 中繼（100~500ms RTT），JSON/JS 壓縮後小 3~4 倍，少跑好幾趟
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.on_event("startup")
async def _peer_startup():
    try:
        if _peer_register():
            loop = asyncio.get_event_loop()
            await loop.start_serving_pipe(_PeerProtocol, _peer["sock"])
            _peer["ok"] = True
            asyncio.get_event_loop().create_task(_peer_touch_loop())
            log.info("peer messaging ready: %s", _peer["sock"])
    except Exception as e:
        _peer_unregister()
        log.warning("peer messaging disabled: %s", e)


@app.on_event("startup")
async def _warm_rooms():
    asyncio.get_event_loop().run_in_executor(None, scan_rooms)


@app.on_event("shutdown")
async def _peer_shutdown():
    _peer_unregister()


@app.middleware("http")
async def require_token(request: Request, call_next):
    """設了 auth_token 就每個請求都要帶。來自本機的連線放行（本機＝已經坐在電腦前）。"""
    token = CONFIG["auth_token"]
    if token:
        client = request.client.host if request.client else ""
        if client not in ("127.0.0.1", "::1"):
            given = (request.headers.get("x-auth-token")
                     or request.query_params.get("token") or "")
            if not secrets.compare_digest(given, token):
                return JSONResponse({"detail": "沒有權限"}, status_code=401)
    return await call_next(request)


@app.middleware("http")
async def _timing(request: Request, call_next):
    """手機那頭喊慢時的量尺：非本機來源每個請求都記耗時；本機只記超過 1 秒的。"""
    t0 = time.perf_counter()
    resp = await call_next(request)
    dt = time.perf_counter() - t0
    client = request.client.host if request.client else ""
    if client not in ("127.0.0.1", "::1") or dt > 1.0:
        log.info("req %s %s %s -> %s %.3fs", client, request.method, request.url.path, resp.status_code, dt)
    return resp


class SendBody(BaseModel):
    text: str
    slug: str | None = None
    sid: str | None = None
    project: str | None = None
    mode: str = ""          # 空字串 = 用 config.json 的 default_mode
    model: str | None = None
    effort: str | None = None
    engine: str | None = None


@app.get("/api/health")
def health():
    return {"ok": True, "claude": CLAUDE_ARGV[0], "projects": PROJECTS_DIR.exists(),
            "desktop_app": desktop_app_available(),
            "desktop_registry": bool(desktop_registry()),
            "desktop_sync": CONFIG["desktop_sync"],
            "perm_ready": PERM_READY}


class SettingsBody(BaseModel):
    desktop_sync: str | None = None


@app.post("/api/settings")
def set_settings(body: SettingsBody):
    """手機上能改的伺服器設定（目前只有桌面同步方式），寫回 config.json。"""
    if body.desktop_sync is not None:
        if body.desktop_sync not in ("auto", "manual", "off"):
            raise HTTPException(400, "desktop_sync 只能是 auto / manual / off")
        CONFIG["desktop_sync"] = body.desktop_sync
        try:
            user = json.loads(CONFIG_FILE.read_text("utf-8"))
            if not isinstance(user, dict):
                user = {}
        except Exception:
            user = {}
        user["desktop_sync"] = body.desktop_sync
        tmp = CONFIG_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(user, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, CONFIG_FILE)
    return {"ok": True, "desktop_sync": CONFIG["desktop_sync"]}


def _upload_dir_size():
    try:
        return sum(f.stat().st_size for f in UPLOAD_DIR.glob("*") if f.is_file())
    except OSError:
        return 0


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    """手機上傳截圖/照片，回傳本機路徑（訊息裡引用，AI 用 Read 看圖）。"""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in UPLOAD_EXTS:
        raise HTTPException(400, "只收圖片（png/jpg/gif/webp）或文件（pdf/txt/md/csv/json）")
    UPLOAD_DIR.mkdir(exist_ok=True)
    if _upload_dir_size() >= UPLOAD_QUOTA:
        raise HTTPException(507, "上傳資料夾已滿，先清一下 uploads/")

    name = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:12] + ext
    dest = UPLOAD_DIR / name
    total = 0
    head = b""
    try:
        # 邊收邊寫邊算，不要先整包讀進記憶體再檢查大小
        with open(dest, "wb") as out:
            while True:
                chunk = await file.read(UPLOAD_CHUNK)
                if not chunk:
                    break
                if not head:
                    head = chunk[:16]
                total += len(chunk)
                if total > UPLOAD_MAX:
                    raise HTTPException(413, "圖片太大（上限 25MB）")
                out.write(chunk)
        if total == 0:
            raise HTTPException(400, "空檔案")
        if ext == ".pdf":
            if not head.startswith(b"%PDF"):
                raise HTTPException(400, "這個檔案的內容不是 PDF")
        elif ext in DOC_EXTS:
            if b"\x00" in head:
                raise HTTPException(400, "這個檔案不是純文字")
        elif not head.startswith(IMAGE_MAGIC):
            raise HTTPException(400, "這個檔案的內容不是圖片")
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        log.exception("upload failed")
        raise HTTPException(500, "存檔失敗") from None
    return {"path": str(dest), "name": name, "size": total}


@app.get("/api/file")
def serve_file(path: str):
    """把本機檔案端給手機看（影片/圖片預覽用）。開放範圍由 config 決定。"""
    p = Path(path)
    if not p.is_absolute():
        raise HTTPException(400, "要用完整路徑")
    try:
        rp = p.resolve(strict=True)
    except OSError:
        raise HTTPException(404, "檔案不存在") from None
    low = str(rp).casefold()
    if not any(low.startswith(root) for root in ALLOWED_FILE_ROOTS):
        raise HTTPException(403, "這個位置不開放")
    if not rp.is_file():
        raise HTTPException(404, "不是檔案")

    ext = rp.suffix.lower()
    if ext in ACTIVE_CONTENT_EXTS or ext not in INLINE_OK_EXTS:
        # filename 一設，Starlette 會加 Content-Disposition: attachment，
        # 瀏覽器就不會把它當同源網頁執行
        return FileResponse(str(rp), filename=rp.name,
                            media_type="application/octet-stream")
    return FileResponse(str(rp))


@app.get("/api/rooms")
def rooms(all: int = 0):
    return {"rooms": scan_rooms(show_all=bool(all)), "pending_perms": pending_perms()}


@app.get("/api/projects")
def projects():
    seen = {}
    for r in scan_rooms():
        p = r.get("project")
        if p and p not in seen and Path(p).is_dir():
            seen[p] = {"path": p, "name": r["project_name"], "slug": r["slug"]}
    return {"projects": list(seen.values())}


@app.get("/api/engines")
def engines():
    keys = load_keys()
    out = []
    for eid, spec in ENGINES.items():
        row = {"id": eid, "label": spec["label"], "kind": spec["kind"],
               "icon": spec["icon"], "note": spec["note"]}
        if spec["kind"] == "api":
            row["has_key"] = bool(keys.get(eid, {}).get("key"))
            row["model"] = keys.get(eid, {}).get("model") or spec["model"]
        else:
            row["ready"] = True if eid == "claude" else Path(CODEX_EXE).exists()
        out.append(row)
    return {"engines": out}


class KeyBody(BaseModel):
    key: str | None = None
    model: str | None = None


@app.post("/api/engines/{eid}/key")
def set_key(eid: str, body: KeyBody):
    if eid not in ENGINES or ENGINES[eid]["kind"] != "api":
        raise HTTPException(400, "沒有這個引擎")
    keys = load_keys()
    cur = dict(keys.get(eid) or {})
    if body.key is not None:
        k = body.key.strip()
        if k:
            cur["key"] = k
        else:
            cur.pop("key", None)
    if body.model is not None:
        m = body.model.strip()
        if m:
            cur["model"] = m
        else:
            cur.pop("model", None)
    keys[eid] = cur
    save_keys(keys)
    return {"ok": True, "has_key": bool(cur.get("key")),
            "model": cur.get("model") or ENGINES[eid]["model"]}


@app.get("/api/history/{slug}/{sid}")
def history(slug: str, sid: str, before: int | None = None, limit: int = 120):
    f = find_room_file(slug, sid)
    eng = SLUG_ENGINE.get(slug)
    if eng == "codex":
        out = codex_history(f, limit=min(limit, 400))
    elif eng:
        out = api_history(f)
    else:
        out = load_history(f, before=before, limit=min(limit, 400))
    out["running_run_id"] = BY_SESSION.get(sid)
    run = RUNS.get(out["running_run_id"]) if out["running_run_id"] else None
    out["n_events"] = len(run.events) if run else 0
    out["busy"] = (not eng) and _room_busy(f)
    out["run"] = ({"run_id": out["running_run_id"], "n_events": out["n_events"],
                   "peer": bool(getattr(run, "peer", False))} if run else None)
    return out


@app.get("/api/tail/{slug}/{sid}")
def tail(slug: str, sid: str, offset: int = 0):
    """手機旁觀桌面正在跑的對話：回 offset 之後的新內容＋它是不是還在忙。"""
    f = find_room_file(slug, sid)
    if SLUG_ENGINE.get(slug):
        return {"items": [], "offset": offset, "busy": False, "live": False}
    items, new_off = tail_items(f, max(0, offset))
    return {"items": items, "offset": new_off, "busy": _room_busy(f),
            "live": live_peer_for(sid) is not None, "running": sid in BY_SESSION,
            "pending": pending_perms(sid), "answered": pending_perms(sid, answered=True)}


@app.get("/api/bg/{slug}/{sid}")
def bg_tasks(slug: str, sid: str):
    f = find_room_file(slug, sid)
    if SLUG_ENGINE.get(slug) or not f.exists():
        return {"tasks": []}
    return list_bg_tasks(f, sid)


@app.get("/api/limits")
def limits():
    return plan_limits()


@app.get("/api/usage")
def usage(days: int = 7):
    return token_usage(days)


# ---------- AskUserQuestion 橋接 ----------
# claude -p 沒有介面可以回答 AskUserQuestion，工具呼叫會直接失敗。
# 橋接法：PreToolUse hook 攔下它 → POST 到這裡 → SSE 推給手機出選項卡 →
# 使用者點選 → hook 長輪詢拿到答案 → 以 deny+reason 把選擇還給模型。

ASKS = {}  # ask_id -> {"run_id", "questions", "answer", "created"}


class AskBody(BaseModel):
    run_id: str
    tool_input: dict


class AskAnswerBody(BaseModel):
    answers: dict = {}
    free_text: str = ""
    skipped: bool = False


@app.post("/api/ask")
async def ask_open(body: AskBody):
    run = RUNS.get(body.run_id)
    if not run or run.done:
        raise HTTPException(404, "這個工作已經結束")
    ask_id = uuid.uuid4().hex[:12]
    questions = body.tool_input.get("questions") or []
    ASKS[ask_id] = {"run_id": body.run_id, "questions": questions,
                    "answer": None, "created": time.time()}
    await _emit(run, {"kind": "ask", "ask_id": ask_id, "questions": questions})
    return {"ask_id": ask_id}


@app.get("/api/ask/{ask_id}")
async def ask_poll(ask_id: str):
    """hook 的長輪詢端點：最多等 20 秒，拿到答案或先回 pending。"""
    a = ASKS.get(ask_id)
    if not a:
        raise HTTPException(404, "沒有這筆提問")
    for _ in range(40):
        if a["answer"] is not None:
            return {"answer": a["answer"]}
        run = RUNS.get(a["run_id"])
        if not run or run.done:
            return {"answer": {"skipped": True, "reason": "run_ended"}}
        await asyncio.sleep(0.5)
    return {"pending": True}


@app.post("/api/ask/{ask_id}/answer")
async def ask_answer(ask_id: str, body: AskAnswerBody):
    a = ASKS.get(ask_id)
    if not a:
        raise HTTPException(404, "沒有這筆提問")
    if a["answer"] is not None:
        return {"ok": True, "already": True}
    a["answer"] = {"answers": body.answers, "free_text": body.free_text,
                   "skipped": body.skipped}
    run = RUNS.get(a["run_id"])
    if run:
        await _emit(run, {"kind": "ask_done", "ask_id": ask_id})
    return {"ok": True}


class ArchiveBody(BaseModel):
    on: bool = True


@app.post("/api/room/{slug}/{sid}/archive")
def room_archive(slug: str, sid: str, body: ArchiveBody):
    find_room_file(slug, sid)
    d = dict(load_overlay())
    d[sid] = "archived" if body.on else "active"
    save_overlay(d)
    return {"ok": True, "archived": body.on}


@app.post("/api/room/{slug}/{sid}/delete")
def room_delete(slug: str, sid: str):
    """刪除＝移到 trash/ 資料夾（可手動救回），不直接銷毀。"""
    if sid in BY_SESSION:
        raise HTTPException(409, "這個聊天室還在忙，先停掉再刪")
    f = find_room_file(slug, sid)
    TRASH_DIR.mkdir(exist_ok=True)
    dest = TRASH_DIR / (slug + "__" + f.name)
    if dest.exists():
        dest = TRASH_DIR / (slug + "__" + uuid.uuid4().hex[:6] + "-" + f.name)
    shutil.move(str(f), str(dest))
    _room_cache.pop(str(f), None)
    d = dict(load_overlay())
    d.pop(sid, None)
    save_overlay(d)
    return {"ok": True, "trash": str(dest)}


@app.post("/api/room/{slug}/{sid}/desktop")
def room_desktop(slug: str, sid: str):
    """把這個聊天室登錄進桌面 app 並切過去（只有 Claude 對話才有 jsonl 可以匯）。"""
    if SLUG_ENGINE.get(slug):
        raise HTTPException(400, "只有 Claude Code 的對話能同步到桌面 app")
    find_room_file(slug, sid)
    try:
        res = desktop_open(sid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        log.exception("desktop_open failed")
        raise HTTPException(500, f"開不起來：{e}") from e
    if res == "unavailable":
        raise HTTPException(400, "這台電腦沒有裝 Claude 桌面 app")
    return {"ok": True, "result": res}


class TitleBody(BaseModel):
    title: str = ""


@app.post("/api/room/{slug}/{sid}/title")
def room_title(slug: str, sid: str, body: TitleBody):
    """改聊天室名字（存在 web-titles.json，空字串 = 還原）。"""
    find_room_file(slug, sid)
    t = _clean_title(body.title)
    save_title(sid, t)
    return {"ok": True, "title": t}


@app.get("/api/search")
def search(q: str = "", all: int = 0):
    q = (q or "").strip()
    if len(q) < 2:
        raise HTTPException(400, "至少兩個字")
    return search_rooms(q, show_all=bool(all))


class PermBody(BaseModel):
    run_id: str = ""
    session_id: str = ""   # 桌面／CLI 的 run 帶這個（hook stdin 的 session_id），run_id 留空
    cwd: str = ""
    tool_name: str = ""
    tool_input: dict = {}
    reason: str = ""   # 例如危險指令攔截 hook 給的說明，卡片上會用警示樣式顯示
    notify_only: bool = False   # 桌面 session 的 PreToolUse 危險指令 hook：只登記卡片（它自己回 ask 讓桌面 app 跳框）
    event: str = ""             # "PermissionRequest" = 與桌面確認框並行的 hook，會等手機答案；同一題會接上既有卡片


class PermAnswerBody(BaseModel):
    decision: str = "deny"   # allow / deny / allow_all / ask（hook 逾時，改回桌面 app 自己問）
    by: str = ""             # phone（預設）/ desktop（桌面原生確認框先按了）/ timeout
    answers: dict = {}       # AskUserQuestion：問題文字 -> 選的答案（桌面版是靠確認框回填 answers 的，手機也走同一條）
    free_text: str = ""      # AskUserQuestion：打字作答


@app.post("/api/perm")
async def perm_open(body: PermBody):
    run = RUNS.get(body.run_id) if body.run_id else None
    if body.run_id:
        if not run or run.done:
            raise HTTPException(404, "這個工作已經結束")
        if run.allow_all:
            return {"perm_id": None, "decision": "allow"}
    else:
        if not _safe_name(body.session_id, r"[A-Za-z0-9-]+"):
            raise HTTPException(400, "缺 session_id")
        if body.event == "PermissionRequest":
            # 危險指令 hook 可能已經替同一題開了卡（帶 reason）→ 接上它，不要開第二張
            cmd = _perm_key(body.tool_input)
            for pid, p in PERMS.items():
                if (p.get("sid") == body.session_id and p["answer"] is None and p.get("run_id") is None
                        and p["tool"] == body.tool_name and (not cmd or p.get("command") == cmd)):
                    p["notify_only"] = False
                    return {"perm_id": pid}
    perm_id = uuid.uuid4().hex[:12]
    detail = _tool_detail(body.tool_name, body.tool_input)
    preview = ""
    if isinstance(body.tool_input, dict):
        for k in ("command", "content", "new_string", "url"):
            v = body.tool_input.get(k)
            if isinstance(v, str) and v.strip():
                preview = v[:600]
                break
    PERMS[perm_id] = {"run_id": body.run_id or None, "sid": body.session_id or (run.sid if run else None),
                      "tool": body.tool_name, "detail": detail, "preview": preview,
                      "reason": (body.reason or "")[:800], "answer": None, "by": "", "created": time.time(),
                      "notify_only": bool(body.notify_only), "cwd": body.cwd or "",
                      "command": _perm_key(body.tool_input),
                      "questions": (body.tool_input.get("questions") if body.tool_name == "AskUserQuestion"
                                    and isinstance(body.tool_input, dict) else None)}
    card = _perm_card(perm_id, PERMS[perm_id])
    if run:
        await _emit(run, card)
    else:
        # 桌面 session：這個房若剛好有手機端的工作在跑（直送模式），也塞進它的事件流
        rid = BY_SESSION.get(body.session_id)
        if rid and rid in RUNS and not RUNS[rid].done:
            await _emit(RUNS[rid], card)
    return {"perm_id": perm_id}


@app.get("/api/perm/{perm_id}")
async def perm_poll(perm_id: str):
    p = PERMS.get(perm_id)
    if not p:
        raise HTTPException(404, "沒有這筆授權")
    for _ in range(40):
        if p["answer"] is not None:
            return {"decision": p["answer"], "answers": p.get("answers") or {}, "free_text": p.get("free_text") or ""}
        if p.get("run_id"):
            run = RUNS.get(p["run_id"])
            if not run or run.done:
                return {"decision": "deny", "reason": "run_ended"}
            if run.allow_all:
                return {"decision": "allow"}
        await asyncio.sleep(0.5)
    return {"pending": True}


@app.post("/api/perm/{perm_id}/answer")
async def perm_answer(perm_id: str, body: PermAnswerBody):
    p = PERMS.get(perm_id)
    if not p:
        raise HTTPException(404, "沒有這筆授權")
    if body.decision not in ("allow", "deny", "allow_all", "ask"):
        raise HTTPException(400, "decision 只能是 allow / deny / allow_all / ask")
    if p["answer"] is not None:
        return {"ok": True, "already": True}
    run = RUNS.get(p["run_id"]) if p.get("run_id") else None
    if body.decision == "allow_all" and run:
        run.allow_all = True
    p["answer"] = "allow" if body.decision == "allow_all" else body.decision
    p["by"] = body.by if body.by in ("desktop", "timeout") else "phone"
    p["answers"] = {str(k)[:500]: str(v)[:500] for k, v in (body.answers or {}).items()}
    p["free_text"] = (body.free_text or "")[:2000]
    if run:
        await _emit(run, {"kind": "perm_done", "perm_id": perm_id, "decision": p["answer"], "by": p["by"], "tool": p["tool"]})
    return {"ok": True}


@app.get("/api/status")
def status():
    running = {}
    for sid, rid in BY_SESSION.items():
        run = RUNS.get(rid)
        running[sid] = {"run_id": rid, "n_events": len(run.events) if run else 0,
                        "peer": bool(run and getattr(run, "peer", False))}
    return {"running": running}


@app.post("/api/send")
async def send(body: SendBody):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "沒有內容")
    # 未知的權限模式一律拒絕，不能靜靜退回成「讓 AI 免詢問執行」
    mode = body.mode or CONFIG["default_mode"]
    if mode not in PERMISSION_FLAGS:
        raise HTTPException(400, "不認得這個權限模式")
    # 算真正還在跑的 run，不要算 BY_SESSION —— 新聊天室在拿到 session id 之前
    # 不在那張表裡，用它當上限等於沒有上限
    if sum(1 for r in RUNS.values() if not r.done) >= MAX_CONCURRENT_RUNS:
        raise HTTPException(429, "同時進行的工作太多，等一件做完再送")
    engine = body.engine or SLUG_ENGINE.get(body.slug or "") or "claude"
    if engine not in ENGINES:
        raise HTTPException(400, "沒有這個 AI")

    if body.sid:
        if body.sid in BY_SESSION:
            busy = RUNS.get(BY_SESSION[body.sid])
            if busy and not busy.done and getattr(busy, "peer", False):
                # 桌面行程工作中，手機插話：打斷目前動作、把這句排到最前面，手機繼續追同一條事件流
                try:
                    await peer_interrupt(busy, text)
                except Exception as e:
                    raise HTTPException(500, f"插話失敗：{e}") from e
                await _emit(busy, {"kind": "note", "text": "已打斷桌面目前的動作並插話，等它回應…"})
                return {"run_id": busy.id, "peer": True, "interrupted": True}
            raise HTTPException(409, "這個聊天室還在忙，等它回完")
        f = find_room_file(body.slug, body.sid)
        if engine == "codex":
            cwd = _codex_info(f)["cwd"] or str(HOME)
        elif ENGINES[engine]["kind"] == "api":
            cwd = str(HOME)
        else:
            cwd = _head_info(f)["cwd"]
        if not cwd or not Path(cwd).is_dir():
            cwd = str(HOME)
    else:
        if ENGINES[engine]["kind"] == "api":
            cwd = str(HOME)
        elif not body.project or not Path(body.project).is_dir():
            raise HTTPException(400, "要先選一個專案資料夾")
        else:
            cwd = body.project

    if ENGINES[engine]["kind"] == "api":
        run = Run(uuid.uuid4().hex[:12], API_SLUG[engine], body.sid, cwd)
        RUNS[run.id] = run
        if body.sid:
            BY_SESSION[body.sid] = run.id
        asyncio.get_event_loop().create_task(run_api(run, text, engine, body.model))
        return {"run_id": run.id}

    if engine == "codex":
        if not Path(CODEX_EXE).exists():
            raise HTTPException(400, "這台電腦沒有安裝 Codex")
        run = Run(uuid.uuid4().hex[:12], "codex", body.sid, cwd)
        RUNS[run.id] = run
        if body.sid:
            BY_SESSION[body.sid] = run.id
        asyncio.get_event_loop().create_task(run_codex(run, text, mode))
        return {"run_id": run.id}

    extra = []
    if body.model in MODELS:
        extra += ["--model", MODELS[body.model]]
    if body.effort in EFFORTS:
        extra += ["--effort", body.effort]

    # 桌面 app 正開著這個對話 → 直送進那個行程，不另起第二個行程搶同一份紀錄
    peer = live_peer_for(body.sid) if body.sid else None
    if peer:
        run = Run(uuid.uuid4().hex[:12], body.slug, body.sid, cwd)
        run.peer = True
        RUNS[run.id] = run
        BY_SESSION[body.sid] = run.id
        asyncio.get_event_loop().create_task(run_peer(run, text, peer, f, fallback=(mode, extra)))
        return {"run_id": run.id, "peer": True}

    run = Run(uuid.uuid4().hex[:12], body.slug, body.sid, cwd)
    RUNS[run.id] = run
    if body.sid:
        BY_SESSION[body.sid] = run.id
    asyncio.get_event_loop().create_task(run_claude(run, text, mode, extra))
    return {"run_id": run.id}


@app.get("/api/run/{run_id}/events")
async def run_events(run_id: str, request: Request, start: int = 0):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "這個工作已經結束或不存在")

    async def gen():
        i = max(0, start)
        while True:
            if await request.is_disconnected():
                return
            while i < len(run.events):
                ev = run.events[i]
                yield f"id: {i}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                i += 1
                if ev.get("kind") == "done":
                    return
            if run.done:
                return
            try:
                async with run.cond:
                    await asyncio.wait_for(run.cond.wait(), timeout=15)
            except asyncio.TimeoutError:
                yield ": ping\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.post("/api/run/{run_id}/stop")
async def stop_run(run_id: str):
    run = RUNS.get(run_id)
    if not run or run.done:
        return {"ok": False, "error": "這個工作已經結束了"}
    if getattr(run, "peer", False):
        # 直送桌面的工作：停止＝打斷桌面正在做的動作（等同桌面按 Esc），手機繼續看它怎麼收尾
        try:
            await peer_interrupt(run, "使用者從手機按了停止：立刻停下目前的動作，不要繼續，"
                                      "用一句話說明停在哪裡，然後等下一句指示。")
        except Exception as e:
            log.warning("peer interrupt failed: %s", e)
            run.proc = "stop"
            return {"ok": False, "error": f"打斷失敗，改為不再追蹤：{e}"}
        await _emit(run, {"kind": "note", "text": "已打斷桌面正在做的動作，等它收尾…"})
        return {"ok": True, "interrupted": True}
    if not run.proc:
        # API 引擎跑在 executor 裡，沒有可以殺的子行程
        return {"ok": False, "error": "這種對話停不下來，等它回完"}
    try:
        r = subprocess.run(["taskkill", "/F", "/T", "/PID", str(run.proc.pid)],
                           capture_output=True, timeout=15,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode != 0:
            msg = (r.stderr or b"").decode("utf-8", "replace").strip()[:200]
            log.warning("taskkill rc=%s: %s", r.returncode, msg)
    except Exception as e:
        log.warning("taskkill failed: %s", e)
        return {"ok": False, "error": "停不掉，請看伺服器 log"}
    # 確認行程真的結束了才回報成功，不要按了停止卻還在背景跑
    try:
        await asyncio.wait_for(run.proc.wait(), timeout=10)
    except asyncio.TimeoutError:
        log.warning("run %s: 行程沒有在 10 秒內結束", run_id)
        return {"ok": False, "error": "停止指令送出了，但行程還沒結束"}
    except Exception:
        pass
    return {"ok": True}


# ---------- 前端 JS 組裝 ----------
# 前端程式拆在 static/js/ 底下幾個檔（見 JS_PARTS 的順序，等同以前 app.js 由上到下的段落）。
# 瀏覽器仍然只抓一支 /static/app.js：手機常在慢連線上，拆檔不能變成多跑八趟。
# 這條路由要在 StaticFiles 掛上去之前宣告才會先命中。
JS_PARTS = ["util", "state", "rooms", "chat", "cards", "sheets", "viewer", "bg", "main"]
_js_cache = {"key": None, "body": b"", "etag": ""}


@app.get("/static/app.js")
def app_js(request: Request):
    files = [STATIC / "js" / (p + ".js") for p in JS_PARTS]
    key = tuple((f.stat().st_mtime_ns, f.stat().st_size) for f in files)
    if key != _js_cache["key"]:
        # 用 ; 接：前一檔若忘了分號、下一檔又以 ( 開頭，直接相接會被當成函式呼叫
        body = "\n;\n".join(f.read_text("utf-8") for f in files).encode("utf-8")
        _js_cache.update(key=key, body=body, etag='"' + hashlib.sha1(body).hexdigest()[:16] + '"')
    headers = {"ETag": _js_cache["etag"], "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == _js_cache["etag"]:
        return Response(status_code=304, headers=headers)
    return Response(_js_cache["body"], media_type="application/javascript; charset=utf-8", headers=headers)


# ---------- 靜態頁 ----------

app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
def touch_icon():
    return FileResponse(STATIC / "icon-180.png")
