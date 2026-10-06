"""直送桌面通道：登記檔清理、判斷桌面行程活著、訊息包裝、狀態回報協定、尾讀回覆到什麼時候算做完、管道壞了退回 claude -p。
named pipe 與 Windows 行程查詢全部換成假的；時間相關的常數縮短，整個檔跑完不到一秒。"""
import asyncio
import json
import os

import pytest
from helpers import dump

from claude_chat import peer as PEER
from claude_chat.runs import BY_SESSION, RUNS, Run

SID = "ffffffff-0000-0000-0000-000000000006"
SOCK = r"\\.\pipe\LOCAL\cc-msg-test"
_real_wait_for = asyncio.wait_for   # 有測試會把 asyncio.wait_for 換成縮短版，測試自己等結果要用原版


async def _fast_wait_for(aw, timeout):
    return await _real_wait_for(aw, min(timeout, 0.05))


def test_sweep_removes_only_dead_claude_chat_registrations(tmp_path, monkeypatch):
    monkeypatch.setattr(PEER, "LIVE_DIR", tmp_path)
    monkeypatch.setattr(PEER, "_pid_alive", lambda pid: pid == 1)
    for pid, entry in ((1, "claude-chat"), (2, "claude-chat"), (3, "cli")):
        (tmp_path / f"{pid}.json").write_text(json.dumps({"pid": pid, "entrypoint": entry}), "utf-8")
        (tmp_path / f"{pid}.abc.key").write_text("{}", "utf-8")
    (tmp_path / "bad.json").write_text("not json", "utf-8")
    PEER._sweep_dead_peers()
    # 1 還活著留著；2 死了連 .key 一起清；3 是別人的（就算 pid 死了）不動；壞掉的檔跳過
    assert sorted(p.name for p in tmp_path.iterdir()) == ["1.abc.key", "1.json", "3.abc.key", "3.json", "bad.json"]


# ---------- 觀測 mod 的回報 ----------

def test_observe_tracks_turns_and_resolves_the_oldest_inflight_receipt(monkeypatch):
    monkeypatch.setattr(PEER, "OBS", {})
    monkeypatch.setitem(PEER._peer, "inflight", {})
    monkeypatch.setitem(PEER._peer, "status", {})
    assert PEER.observe(SID, "turn.start", ts=10.0, turn_id="t1") is False
    o = PEER.observed(SID)
    assert o["executing"] is True and o["turn_id"] == "t1" and o["since"] == 10.0 and o["last_kind"] == "turn.start"
    PEER.observe(SID, "turn.complete", ts=12.0, turn_id="t1", reason="answer")
    o = PEER.observed(SID)
    assert o["executing"] is False and o["reason"] == "answer" and o["last"] == 12.0
    # 沒有直送在等回條：抵達回報只記錄，對不到東西
    assert PEER.observe(SID, "receive") is False
    # 兩筆直送在等：一次抵達只把最早那筆標 delivered，第二筆不動；別的對話的回報不影響這間
    loop = asyncio.new_event_loop()
    f1, f2 = loop.create_future(), loop.create_future()
    PEER._peer["status"].update({"m1": f1, "m2": f2})
    PEER._peer["inflight"][SID] = ["m1", "m2"]
    assert PEER.observe("other-sid", "receive") is False and not f1.done()
    assert PEER.observe(SID, "receive") is True
    assert f1.result() == "delivered" and not f2.done()
    assert PEER.observe(SID, "receive") is True and f2.result() == "delivered"
    loop.close()
    assert set(PEER.observed_all()) == {SID, "other-sid"}


# ---------- 桌面行程活著嗎 ----------

@pytest.fixture
def live(tmp_path, monkeypatch):
    """假的 ~/.claude/sessions：寫登記檔用，行程存活與建立時間都可控。"""
    monkeypatch.setattr(PEER, "LIVE_DIR", tmp_path)
    alive = {4242: "111"}   # pid -> procStart
    monkeypatch.setattr(PEER, "_pid_alive", lambda pid: pid in alive)
    monkeypatch.setattr(PEER, "_proc_start_ft", lambda pid: alive.get(pid, "0"))
    monkeypatch.setitem(PEER._peer, "ok", True)

    def reg(pid, sid=SID, sock=SOCK, proc_start="111", key=True, token="tok", name="desk"):
        d = {"pid": pid, "sessionId": sid, "messagingSocketPath": sock, "procStart": proc_start, "name": name, "cwd": "C:\\w"}
        (tmp_path / f"{pid}.json").write_text(json.dumps(d), "utf-8")
        for old in tmp_path.glob(f"{pid}.*.key"):
            old.unlink()
        if key:
            (tmp_path / f"{pid}.deadbeef.key").write_text(json.dumps({"peerToken": token}), "utf-8")
    return {"dir": tmp_path, "alive": alive, "reg": reg}


def test_live_peer_for_matches_session_with_living_process_and_key(live):
    live["reg"](4242)
    assert PEER.live_peer_for(SID) == {"pid": 4242, "sock": SOCK, "token": "tok", "name": "desk", "cwd": "C:\\w"}
    assert PEER.live_peer_for("other-sid") is None


def test_live_peer_for_rejects_dead_reused_own_or_keyless_processes(live):
    live["reg"](4242, proc_start="999")   # pid 被回收再用：登記的建立時間跟現在這隻不同
    assert PEER.live_peer_for(SID) is None
    live["reg"](4242, key=False)
    assert PEER.live_peer_for(SID) is None
    live["reg"](5000)   # 行程已死
    assert PEER.live_peer_for(SID) is None
    live["reg"](os.getpid(), proc_start="x")   # 自己不算
    assert PEER.live_peer_for(SID) is None
    live["reg"](4242, token="")
    assert PEER.live_peer_for(SID) is None
    live["reg"](4242)
    assert PEER.live_peer_for(SID)["pid"] == 4242
    PEER._peer["ok"] = False   # 本伺服器自己沒登記成功 → 一律不直送
    assert PEER.live_peer_for(SID) is None


# ---------- 訊息包裝 ----------

def test_peer_frames_wrap_text_with_hint_and_desktop_mode(monkeypatch):
    monkeypatch.setitem(PEER._peer, "sock", SOCK)
    monkeypatch.setattr(PEER, "desktop_registry", lambda: {SID: {"permissionMode": "plan"}})
    frames = PEER._peer_frames({"token": "tok"}, SID, "推上去", "now", "m1")
    assert frames[0] == {"type": "auth", "token": "tok"}
    f = frames[1]
    assert f["msg_id"] == "m1" and f["priority"] == "now" and f["from"] == "uds:" + SOCK and f["msgV"] == 1
    body = f["message"]["content"]
    assert body.startswith('<cross-session-message from="uds:' + SOCK + '" from-name="phone" from-mode="plan">\n推上去\n\n')
    assert PEER.PEER_HINT in body and body.endswith("</cross-session-message>")
    # 桌面是全自動或沒登記 → bypass；msg_id 沒給就自己產
    monkeypatch.setattr(PEER, "desktop_registry", lambda: {SID: {"permissionMode": "bypassPermissions"}})
    assert 'from-mode="bypass"' in PEER._peer_frames({"token": "t"}, SID, "x")[1]["message"]["content"]
    monkeypatch.setattr(PEER, "desktop_registry", lambda: {})
    f2 = PEER._peer_frames({"token": "t"}, SID, "x")[1]
    assert 'from-mode="bypass"' in f2["message"]["content"] and len(f2["msg_id"]) == 36 and f2["priority"] == "next"


# ---------- pipe 上的狀態回報協定 ----------

class _Transport:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_peer_protocol_requires_auth_then_resolves_status_futures(monkeypatch):
    monkeypatch.setitem(PEER._peer, "token", "secret")
    bad = PEER._PeerProtocol()
    bad.connection_made(_Transport())
    bad.data_received(b'{"type":"auth","token":"wrong"}\n')
    assert bad.transport.closed and not bad.authed

    async def go():
        fut = asyncio.get_event_loop().create_future()
        PEER._peer["status"]["m1"] = fut
        good = PEER._PeerProtocol()
        good.connection_made(_Transport())
        good.data_received(b'{"type":"auth","tok')   # 半行先到
        assert not good.authed
        good.data_received(b'en":"secret"}\n{"type":"control","action":"peer_message_status",'
                           b'"orig_msg_id":"m1","status":"delivered"}\nnot json\n')
        assert good.authed and not good.transport.closed
        return fut.result()
    try:
        assert asyncio.run(go()) == "delivered"
    finally:
        PEER._peer["status"].clear()


# ---------- run_peer：直送＋尾讀 ----------

@pytest.fixture
def peer_env(tmp_path, monkeypatch):
    monkeypatch.setitem(PEER._peer, "sock", SOCK)
    monkeypatch.setattr(PEER, "desktop_registry", lambda: {})
    monkeypatch.setattr(PEER, "PEER_FIRST_WAIT", 0.3)
    monkeypatch.setattr(PEER, "PEER_IDLE_GAP", 0.05)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s: real_sleep(min(s, 0.02)))   # 尾讀迴圈每秒一圈 → 20ms

    async def _no_gc(run_id, delay=0):
        RUNS.pop(run_id, None)
    monkeypatch.setattr(PEER, "_gc_run", _no_gc)

    sent = []
    box = {"sent": sent, "pipe_error": None, "status": "delivered", "fallback_calls": []}

    def fake_pipe_send(sock, frames):
        if box["pipe_error"]:
            raise box["pipe_error"]
        sent.append((sock, frames))
    monkeypatch.setattr(PEER, "_pipe_send", fake_pipe_send)

    async def fake_run_claude(run, text, mode, extra=()):
        box["fallback_calls"].append((run.id, text, mode, tuple(extra)))
        await PEER._emit(run, {"kind": "done", "ok": True, "sid": run.sid, "error": ""})
    monkeypatch.setattr(PEER, "run_claude", fake_run_claude)

    path = tmp_path / (SID + ".jsonl")
    path.write_bytes(b"")
    box["path"] = path
    box["peer"] = {"pid": 4242, "sock": SOCK, "token": "tok", "name": "desk", "cwd": "C:\\w"}
    yield box
    PEER._peer["status"].clear()
    BY_SESSION.clear()
    RUNS.clear()


def mk_run(run_id="p1"):
    run = Run(run_id, "C--w", SID, "C:\\w")
    run.peer = True
    RUNS[run_id] = run
    BY_SESSION[SID] = run_id
    return run


def append(path, *recs):
    with open(path, "ab") as f:
        for r in recs:
            f.write(dump(r).encode("utf-8"))


def assistant_rec(text, stop_reason="end_turn", tokens=1000):
    return {"type": "assistant", "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": stop_reason,
                                             "content": [{"type": "text", "text": text}],
                                             "usage": {"input_tokens": tokens}}}


async def drive(box, run, text, after_send=None):
    """啟動 run_peer，等它把訊息送出去，回報狀態，然後交給 after_send 往 jsonl 寫桌面的回覆。"""
    task = asyncio.create_task(PEER.run_peer(run, text, box["peer"], box["path"], fallback=("auto", ["--model", "m"])))
    for _ in range(200):
        if box["sent"] or box["fallback_calls"] or task.done():
            break
        await asyncio.sleep(0.01)
    if box["sent"]:
        msg_id = box["sent"][-1][1][1]["msg_id"]
        fut = PEER._peer["status"].get(msg_id)
        if fut and box["status"] is not None:
            fut.set_result(box["status"])
        if after_send:
            await after_send()
    await _real_wait_for(task, 10)
    return run.events


def test_run_peer_delivers_then_tails_reply_until_end_turn(peer_env):
    run = mk_run()
    box = peer_env

    async def desktop_replies():
        append(box["path"],
               {"type": "user", "message": {"role": "user",
                                            "content": "<cross-session-message>\n幫我看一下\n</cross-session-message>"}},
               {"type": "assistant", "message": {"role": "assistant", "stop_reason": "tool_use",
                                                 "content": [{"type": "tool_use", "name": "Read",
                                                              "input": {"file_path": "a.py"}, "id": "t1"}]}},
               {"type": "user", "message": {"role": "user",
                                            "content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": False}]}},
               assistant_rec("看完了"))
    events = asyncio.run(drive(box, run, "幫我看一下", desktop_replies))
    sock, frames = box["sent"][0]
    assert sock == SOCK and frames[0]["token"] == "tok" and "幫我看一下" in frames[1]["message"]["content"]
    assert frames[1]["priority"] == "next" and run.peer_info == box["peer"]
    ks = [e["kind"] for e in events]
    assert ks == ["init", "note", "tool", "tool_ok", "text", "ctx", "done"]
    assert "桌面 App 開著" in events[1]["text"]
    assert events[2]["tool"] == "Read" and events[3] == {"kind": "tool_ok", "tool_use_id": "t1", "ok": True}
    assert events[4]["text"] == "看完了" and events[5]["tokens"] == 1000
    assert events[6] == {"kind": "done", "ok": True, "sid": SID, "error": ""}
    assert SID not in BY_SESSION and "p1" not in RUNS and not PEER._peer["status"]


def test_run_peer_ignores_old_tail_and_half_written_lines(peer_env):
    """只讀送出之後新增的內容；最後沒換行的半行留到下一圈，不會被當成壞掉的紀錄吞掉。"""
    box = peer_env
    append(box["path"], assistant_rec("這是之前的回覆，不能播"))
    run = mk_run()

    async def desktop_replies():
        with open(box["path"], "ab") as f:
            half = dump(assistant_rec("完整的一句")).encode("utf-8")
            f.write(half[:20])
        await asyncio.sleep(0.05)
        with open(box["path"], "ab") as f:
            f.write(half[20:])
    events = asyncio.run(drive(box, run, "x", desktop_replies))
    texts = [e["text"] for e in events if e["kind"] == "text"]
    assert texts == ["完整的一句"] and events[-1]["ok"] is True


def test_run_peer_falls_back_to_claude_p_when_pipe_is_gone(peer_env):
    box = peer_env
    box["pipe_error"] = OSError("pipe not found")
    run = mk_run()
    events = asyncio.run(drive(box, run, "哈囉"))
    assert box["fallback_calls"] == [("p1", "哈囉", "auto", ("--model", "m"))]
    assert run.peer is False and not box["sent"]
    assert [e["kind"] for e in events] == ["init", "done"] and not PEER._peer["status"]
    assert not PEER._peer["inflight"]   # 回條等待名單也要跟著收掉，不然觀測 mod 的抵達回報會對到已結束的直送


def test_run_peer_without_fallback_reports_pipe_error(peer_env):
    box = peer_env
    box["pipe_error"] = OSError("pipe not found")
    run = mk_run()
    asyncio.run(asyncio.wait_for(PEER.run_peer(run, "x", box["peer"], box["path"]), 5))
    assert run.events[-1]["ok"] is False and "直送桌面失敗：OSError" in run.events[-1]["error"]
    assert SID not in BY_SESSION


def test_run_peer_held_message_times_out_with_explanation(peer_env):
    box = peer_env
    box["status"] = "held"
    run = mk_run()
    events = asyncio.run(drive(box, run, "x"))
    assert "扣住等你核准" in events[2]["text"]
    assert events[-1]["ok"] is True and "還沒開始處理" in events[-1]["error"]


def test_run_peer_rejected_message_fails_immediately(peer_env):
    box = peer_env
    box["status"] = "rejected"
    run = mk_run()
    events = asyncio.run(drive(box, run, "x"))
    assert events[-1] == {"kind": "done", "ok": False, "sid": SID, "error": "桌面那邊拒收（rejected）"}


def test_run_peer_status_unknown_when_desktop_never_reports(peer_env, monkeypatch):
    """桌面沒回狀態（6 秒逾時）也要繼續尾讀，不能卡死。"""
    box = peer_env
    box["status"] = None
    monkeypatch.setattr(asyncio, "wait_for", _fast_wait_for)
    run = mk_run()

    async def desktop_replies():
        append(box["path"], assistant_rec("有回"))
    events = asyncio.run(drive(box, run, "x", desktop_replies))
    assert [e["kind"] for e in events] == ["init", "note", "text", "ctx", "done"] and events[-1]["ok"] is True


def test_run_peer_stop_from_phone_only_stops_tailing(peer_env):
    box = peer_env
    run = mk_run()

    async def stop_it():
        run.proc = "stop"
    events = asyncio.run(drive(box, run, "x", stop_it))
    assert events[-1]["ok"] is True and "已停止在手機上追蹤" in events[-1]["error"]


def test_peer_interrupt_sends_priority_now_and_resets_turn(peer_env):
    box = peer_env
    run = mk_run()
    run.peer_info = box["peer"]
    asyncio.run(PEER.peer_interrupt(run, "停一下"))
    sock, frames = box["sent"][0]
    assert frames[1]["priority"] == "now" and "停一下" in frames[1]["message"]["content"]
    assert run.interrupted is True and run.ended_at_reset is True
    run2 = mk_run("p2")
    with pytest.raises(RuntimeError):
        asyncio.run(PEER.peer_interrupt(run2, "x"))


def test_run_peer_interrupt_resets_end_turn_so_new_round_is_awaited(peer_env, monkeypatch):
    """插話之後，已經讀到的 end_turn 不算做完：要等新一輪再 end_turn。
    順序刻意跟真實情況一樣：桌面先 end_turn 並被讀到 → 使用者打斷 → 迴圈消化掉重設旗標 → 新一輪才來。"""
    monkeypatch.setattr(PEER, "PEER_IDLE_GAP", 0.3)   # 讀到 end_turn 之後要有時間插旗子，不然先被判做完
    box = peer_env
    run = mk_run()

    async def wait_until(cond):
        for _ in range(300):
            if cond():
                return
            await asyncio.sleep(0.005)
        raise AssertionError("等不到條件")

    async def flow():
        append(box["path"], assistant_rec("第一輪結束"))
        await wait_until(lambda: any(e.get("text") == "第一輪結束" for e in run.events))
        run.ended_at_reset = True   # 等同 peer_interrupt 做的事
        await wait_until(lambda: run.ended_at_reset is False)   # 迴圈消化了旗標
        await asyncio.sleep(0.4)
        assert not any(e["kind"] == "done" for e in run.events), "打斷後不該用舊的 end_turn 收尾"
        append(box["path"], assistant_rec("第二輪結束"))
    events = asyncio.run(drive(box, run, "x", flow))
    assert [e["text"] for e in events if e["kind"] == "text"] == ["第一輪結束", "第二輪結束"]
    assert events[-1]["ok"] is True


def test_sweep_and_register_roundtrip_unregister(tmp_path, monkeypatch):
    """登記＋取消登記：檔案配對產生、取消後清掉；別人的檔不動。"""
    monkeypatch.setattr(PEER, "LIVE_DIR", tmp_path)
    monkeypatch.setattr(PEER, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(PEER, "_proc_start_ft", lambda pid: "777")
    monkeypatch.setattr(PEER, "sys", type("S", (), {"platform": "win32"}))
    (tmp_path / "9.json").write_text(json.dumps({"pid": 9, "entrypoint": "cli"}), "utf-8")
    saved = dict(PEER._peer)
    try:
        assert PEER._peer_register() is True
        pid = os.getpid()
        reg = json.loads((tmp_path / f"{pid}.json").read_text("utf-8"))
        assert reg["entrypoint"] == "claude-chat" and reg["name"] == "phone" and reg["procStart"] == "777"
        assert reg["messagingSocketPath"] == PEER._peer["sock"]
        keys = list(tmp_path.glob(f"{pid}.*.key"))
        assert len(keys) == 1 and json.loads(keys[0].read_text("utf-8"))["peerToken"] == PEER._peer["token"]
        PEER._peer_unregister()
        assert sorted(p.name for p in tmp_path.iterdir()) == ["9.json"]
    finally:
        PEER._peer.clear()
        PEER._peer.update(saved)
