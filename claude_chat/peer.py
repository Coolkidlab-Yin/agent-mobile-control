# -*- coding: utf-8 -*-
"""桌面開著的對話：手機訊息直送進桌面那個 claude 行程（CLI 的 named pipe 訊息通道），回覆靠尾讀 jsonl。"""
import asyncio
import hashlib
import json
import os
import secrets
import sys
import time
import uuid
from pathlib import Path

from .config import BASE, LIVE_DIR, log
from .desktop import desktop_registry
from .jsonl import _ctx_window, _loads, items_from_message
from .procs import _pid_alive, _proc_start_ft
from .runner import run_claude
from .runs import BY_SESSION, _emit, _gc_run

# ---------- 桌面開著的對話：直送進桌面那個行程（peer messaging） ----------
# 同一個對話若桌面 app 正開著（有活著的 claude 行程），再另起 `claude -p --resume` 會變成兩個行程
# 寫同一份 jsonl：桌面畫面不更新、桌面那邊下一句還會用舊的上下文（09-03 實測中招）。
# 解法：CLI 本機 session 之間有 named pipe 訊息通道（SendMessage 工具走的那條），協定實測：
#   連上 ~/.claude/sessions/<pid>.json 的 messagingSocketPath，第一行 {"type":"auth","token":<對方 .key 檔的 peerToken>}
#   第二行 {"msgV":1,"msg_id","type":"user","message":{"role":"user","content":<cross-session-message 包裝>},
#          "priority":"next","from":"uds:<自己的 pipe>"}
# 收件端會核對連線行程 pid = from 位址登記的 pid，而且要有官方包裝（from-mode）才不會被 held 等核准，
# 所以本伺服器自己也登記成一個 peer（名字 phone），由同一個行程發送。回覆則靠尾讀 jsonl 顯示在手機上。
PEER_NAME = "phone"
_peer = {"ok": False, "sock": None, "token": None, "json": None, "key": None,
         "status": {},     # msg_id -> asyncio.Future(status str)
         "inflight": {}}   # sid -> [msg_id, ...] 正在等回條的直送（觀測 mod 回報「抵達」時靠 sid 對）

# ---------- 觀測 mod 的回報（mods/observer）：桌面對話自己說「我開始跑了／跑完了／手機的訊息到了」 ----------
# 直送回條的真相（2026-10-06 實測）：伺服器 log 全史 123 次直送狀態全是 unknown、接收管道零封包、
# _pipe_send 寫完就關連線不讀回覆——CLI 的回條從來沒到過。改成讓收件端那個對話裡的 mod 在
# session.receive 時 POST /api/observe，這裡把對應的 future 標成 delivered。turn.start/complete 則是
# 「確認執行中」最可靠的來源（比 jsonl mtime 準：壓縮、背景通知也會動檔案）。只在記憶體，重啟歸零。
OBS = {}   # sid -> {"executing": bool, "turn_id", "since", "last", "last_kind", "reason"}
OBS_KINDS = ("turn.start", "turn.complete", "receive")


def observe(sid, kind, ts=None, turn_id="", reason=""):
    """記一筆觀測；kind=receive 時順手把這個對話最早還在等的直送回條標成 delivered。回有沒有對到回條。"""
    ts = ts or time.time()
    o = OBS.setdefault(sid, {"executing": False, "turn_id": "", "since": None, "last": None, "last_kind": "", "reason": ""})
    o.update(last=ts, last_kind=kind)
    if kind == "turn.start":
        o.update(executing=True, turn_id=turn_id, since=ts, reason="")
    elif kind == "turn.complete":
        o.update(executing=False, turn_id=turn_id, reason=reason)
    matched = False
    if kind == "receive":
        for mid in list(_peer["inflight"].get(sid) or []):
            fut = _peer["status"].get(mid)
            if fut and not fut.done():
                fut.set_result("delivered")
                matched = True
                break
    return matched


def observed(sid):
    return OBS.get(sid)


def observed_all():
    return OBS


def _pid_domain():
    return "win32:" + (os.environ.get("USERNAME") or "")


class _PeerProtocol(asyncio.Protocol):
    def __init__(self):
        self.buf = b""
        self.authed = False

    def data_received(self, data):
        self.buf += data
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            rec = _loads(line.decode("utf-8", "replace"))
            if not rec:
                continue
            if not self.authed:
                if rec.get("type") == "auth" and rec.get("token") == _peer["token"]:
                    self.authed = True
                    continue   # auth 之後同一包裡的狀態回報接著處理（原本 return 會把它留到下一包，等於多等 6 秒逾時）
                log.warning("peer pipe: bad auth, closing")
                self.transport.close()
                return
            self._handle(rec)

    def connection_made(self, transport):
        self.transport = transport

    def _handle(self, rec):
        if rec.get("type") == "control" and rec.get("action") == "peer_message_status":
            fut = _peer["status"].get(rec.get("orig_msg_id"))
            if fut and not fut.done():
                fut.set_result(rec.get("status") or "?")
        else:
            log.info("peer pipe: got %s", json.dumps(rec, ensure_ascii=False)[:300])


def _sweep_dead_peers():
    """restart-server.cmd 是硬砍行程，登記檔不會自己清：上一代留下的 claude-chat 登記（pid 已死）在這裡清掉，
    不然別的 session 列 peer 會看到一排死掉的 phone。只動 entrypoint 是 claude-chat 的檔，別人的不碰。"""
    for jf in LIVE_DIR.glob("*.json"):
        try:
            d = json.loads(jf.read_text("utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict) or d.get("entrypoint") != "claude-chat":
            continue
        pid = d.get("pid")
        if not pid or _pid_alive(pid):
            continue
        for f in [jf, *LIVE_DIR.glob(f"{pid}.*.key")]:
            try:
                f.unlink()
            except OSError:
                pass


def _peer_register():
    """把自己登記成 ~/.claude/sessions 裡的一個 peer，並開 pipe 收狀態回報。"""
    if sys.platform != "win32":
        return False
    _sweep_dead_peers()
    pid = os.getpid()
    sock = r"\\.\pipe\LOCAL\cc-msg-" + secrets.token_hex(16)
    ps = _proc_start_ft(pid)
    now = int(time.time() * 1000)
    token = secrets.token_hex(16)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    jf = LIVE_DIR / f"{pid}.json"
    kf = LIVE_DIR / f"{pid}.{hashlib.sha256(sock.lower().encode()).hexdigest()}.key"
    reg = {"pid": pid, "sessionId": str(uuid.uuid4()), "cwd": str(BASE), "startedAt": now,
           "procStart": ps, "version": "2.1.255", "peerProtocol": 1, "peerFeatures": [],
           "kind": "interactive", "entrypoint": "claude-chat", "pidDomain": _pid_domain(),
           "messagingSocketPath": sock, "name": PEER_NAME, "nameSource": "derived",
           "nameSince": now, "updatedAt": now}
    jf.write_text(json.dumps(reg, ensure_ascii=False), "utf-8")
    kf.write_text(json.dumps({"peerToken": token, "procStartFt": ps, "pidDomain": _pid_domain()},
                             ensure_ascii=False), "utf-8")
    _peer.update(sock=sock, token=token, json=jf, key=kf, reg=reg)
    return True


def _peer_unregister():
    for k in ("json", "key"):
        p = _peer.get(k)
        if p:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass


async def _peer_touch_loop():
    """別的 session 列 peer 時會篩 24 小時內有動靜的，每小時摸一下。"""
    while True:
        await asyncio.sleep(3600)
        try:
            reg = dict(_peer["reg"])
            reg["updatedAt"] = int(time.time() * 1000)
            _peer["json"].write_text(json.dumps(reg, ensure_ascii=False), "utf-8")
        except Exception as e:
            log.warning("peer touch failed: %s", e)


def live_peer_for(sid):
    """這個 session 有沒有活著的 claude 行程（桌面 app 開著）＝可以直送。回 dict 或 None。"""
    if not _peer["ok"]:
        return None
    try:
        for p in LIVE_DIR.glob("*.json"):
            try:
                d = json.loads(p.read_text("utf-8"))
            except Exception:
                continue
            if d.get("sessionId") != sid or not d.get("messagingSocketPath"):
                continue
            pid = d.get("pid")
            if not pid or pid == os.getpid() or not _pid_alive(pid):
                continue
            # pid 會被 Windows 回收再用：登記檔的 procStart 要跟現在那個行程的建立時間一致才算同一隻
            ps = d.get("procStart")
            if ps and str(ps) != _proc_start_ft(pid):
                continue
            keys = list(LIVE_DIR.glob(f"{pid}.*.key"))
            if not keys:
                continue
            try:
                token = json.loads(keys[0].read_text("utf-8")).get("peerToken")
            except Exception:
                continue
            if not token:
                continue
            return {"pid": pid, "sock": d["messagingSocketPath"], "token": token,
                    "name": d.get("name") or "", "cwd": d.get("cwd") or ""}
    except OSError:
        pass
    return None


def _pipe_send(sock, lines):
    with open(sock, "r+b", buffering=0) as p:
        for line in lines:
            p.write((json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8"))
        time.sleep(0.3)


PEER_HINT = ("（這句是使用者本人在手機介面打的字。依你的規則它不算本人授權：若這句是在回答你正在等的"
             "決定或授權（推不推、刪不刪、選哪個），不要拒絕也不要直接照做，改用 AskUserQuestion 把同一個決定"
             "再問一次、選項含這句的意思——本人會在手機卡片上按，那才算數。純詢問或不需授權的事照常處理。）")


def _from_mode_for(sid):
    reg = desktop_registry().get(sid) or {}
    pm = reg.get("permissionMode") or "bypassPermissions"
    return "bypass" if pm in ("bypassPermissions", "auto") else pm


def _peer_frames(peer, sid, text, priority="next", msg_id=None):
    """組一則要塞進桌面行程的 user 訊息（auth + user 兩個 frame）。
    priority "now" = 打斷：收件端會 abort 目前的工具／確認框，模型收到 interrupt，這句變成下一輪。"""
    me = "uds:" + _peer["sock"]
    # 收件端的系統規則：跨 session 訊息永遠不算「使用者本人」的授權（防 session 之間互相洗授權）。
    # 所以手機打的字若是在回答一個等待中的決定，桌面那邊不能直接採信——附一行提示，請它改用
    # AskUserQuestion 再問一次；那條走授權元件回填，手機卡片上按的才算本人（見 perm-bridge.js）。
    body = text + "\n\n" + PEER_HINT if PEER_HINT else text
    wrapped = (f'<cross-session-message from="{me}" from-name="{PEER_NAME}" '
               f'from-mode="{_from_mode_for(sid)}">\n{body}\n</cross-session-message>')
    return [
        {"type": "auth", "token": peer["token"]},
        {"msgV": 1, "msg_id": msg_id or str(uuid.uuid4()), "type": "user",
         "message": {"role": "user", "content": wrapped},
         "priority": priority, "from": me},
    ]


async def peer_interrupt(run, text):
    """手機在桌面行程工作中插話：priority now = 先打斷再把這句排進去。"""
    peer = getattr(run, "peer_info", None)
    if not peer:
        raise RuntimeError("這個工作沒有桌面通道")
    await asyncio.to_thread(_pipe_send, peer["sock"], _peer_frames(peer, run.sid, text, "now"))
    run.interrupted = True
    run.ended_at_reset = True


PEER_FIRST_WAIT = 90      # 桌面那邊多久沒開始回就當它在等核准
PEER_IDLE_GAP = 2.5       # end_turn 之後再安靜幾秒才算做完
PEER_MAX_TAIL = 45 * 60


async def run_peer(run, text, peer, path, fallback=None):
    """把訊息直送進桌面開著的那個行程，然後尾讀 jsonl 把它的回覆播到手機。
    pipe 開不起來（登記檔過期、行程剛死）→ 退回原本的 claude -p --resume（fallback=(mode, extra)）。"""
    msg_id = str(uuid.uuid4())
    run.peer_info = peer
    fut = asyncio.get_event_loop().create_future()
    _peer["status"][msg_id] = fut
    _peer["inflight"].setdefault(run.sid, []).append(msg_id)
    try:
        offset = path.stat().st_size
    except OSError:
        offset = 0
    await _emit(run, {"kind": "init", "sid": run.sid})
    try:
        try:
            await asyncio.to_thread(_pipe_send, peer["sock"], _peer_frames(peer, run.sid, text, "next", msg_id))
        except OSError as e:
            # 登記檔還在但 pipe 已經不見（行程剛結束/卡死）→ 當作桌面沒開著，走原本的路
            log.warning("peer pipe unusable for %s (%s); falling back to claude -p", run.sid, e)
            _peer["status"].pop(msg_id, None)
            if fallback is None:
                raise
            run.peer = False
            mode, extra = fallback
            await run_claude(run, text, mode, extra)
            return
        await _emit(run, {"kind": "note",
                          "text": "這個對話在桌面 App 開著：訊息直接送進桌面那邊，回覆由桌面產生（模型/力度設定以桌面為準）"})
        try:
            status = await asyncio.wait_for(fut, 6)
        except asyncio.TimeoutError:
            status = "unknown"
        log.info("peer send %s -> %s (%s)", run.sid, status, peer["name"])
        if status == "held":
            await _emit(run, {"kind": "note",
                              "text": "桌面那邊把這則訊息扣住等你核准（權限模式不同）。到桌面 App 按同意它才會開始做。"})
        elif status not in ("delivered", "unknown"):
            await _emit(run, {"kind": "done", "ok": False, "sid": run.sid,
                              "error": f"桌面那邊拒收（{status}）"})
            return

        # 尾讀 jsonl：看到我們的訊息落地 → 看它回 → end_turn 後安靜一下就算做完
        started = time.time()
        seen_ours = False
        ended_at = None
        last_change = time.time()
        tool_status = {}
        while True:
            await asyncio.sleep(1.0)
            try:
                size = path.stat().st_size
            except OSError:
                size = offset
            if size > offset:
                with open(path, "rb") as f:
                    f.seek(offset)
                    chunk = f.read(size - offset)
                # 只處理完整的行，半行留到下次
                nl = chunk.rfind(b"\n")
                if nl < 0:
                    continue
                offset += nl + 1
                last_change = time.time()
                for line in chunk[:nl].decode("utf-8", "replace").splitlines():
                    rec = _loads(line)
                    if not rec:
                        continue
                    rt = rec.get("type")
                    msg = rec.get("message") or {}
                    if rt == "user":
                        content = msg.get("content")
                        if isinstance(content, list):
                            for b in content:
                                if isinstance(b, dict) and b.get("type") == "tool_result":
                                    await _emit(run, {"kind": "tool_ok", "tool_use_id": b.get("tool_use_id"),
                                                      "ok": not b.get("is_error", False)})
                        elif isinstance(content, str) and text[:40] in content:
                            seen_ours = True
                    elif rt == "assistant":
                        seen_ours = True
                        for it in items_from_message("assistant", msg, tool_status=tool_status):
                            await _emit(run, it)
                        u = msg.get("usage")
                        if isinstance(u, dict):
                            tok = ((u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                                   + (u.get("cache_creation_input_tokens") or 0))
                            if tok > 0:
                                win = _ctx_window(msg.get("model"), tok)
                                await _emit(run, {"kind": "ctx", "tokens": tok, "window": win,
                                                  "pct": round(tok * 100 / win)})
                        if msg.get("stop_reason") in ("end_turn", "stop_sequence"):
                            ended_at = time.time()
                        else:
                            ended_at = None
            now = time.time()
            if getattr(run, "ended_at_reset", False):
                # 剛打斷／插話：舊的 end_turn 不算數，等新一輪
                run.ended_at_reset = False
                ended_at = None
                last_change = now
            if ended_at and now - last_change >= PEER_IDLE_GAP:
                await _emit(run, {"kind": "done", "ok": True, "sid": run.sid, "error": ""})
                return
            if not seen_ours and now - started > PEER_FIRST_WAIT:
                await _emit(run, {"kind": "done", "ok": True, "sid": run.sid,
                                  "error": "桌面那邊還沒開始處理（可能在等你核准，或它正忙）。之後回來這個聊天室就會看到結果。"})
                return
            if now - started > PEER_MAX_TAIL:
                await _emit(run, {"kind": "done", "ok": True, "sid": run.sid, "error": ""})
                return
            if run.proc == "stop":
                await _emit(run, {"kind": "done", "ok": True, "sid": run.sid, "error": "已停止在手機上追蹤（桌面那邊照常繼續）"})
                return
    except Exception as e:
        log.exception("run_peer failed")
        await _emit(run, {"kind": "done", "ok": False, "sid": run.sid,
                          "error": f"直送桌面失敗：{type(e).__name__}: {e}"})
    finally:
        _peer["status"].pop(msg_id, None)
        lst = _peer["inflight"].get(run.sid) or []
        if msg_id in lst:
            lst.remove(msg_id)
        if not lst:
            _peer["inflight"].pop(run.sid, None)
        if run.sid and BY_SESSION.get(run.sid) == run.id:
            BY_SESSION.pop(run.sid, None)
        asyncio.get_event_loop().create_task(_gc_run(run.id))
