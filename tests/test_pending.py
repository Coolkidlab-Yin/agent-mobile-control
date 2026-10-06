"""待答卡落地、等待程式心跳、統一入口 /api/pending、送出去重。"""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from claude_chat import api as A
from claude_chat import perm as P
from claude_chat import store as S
from claude_chat.config import CONFIG
from claude_chat.runs import BY_SESSION, RUNS, _emit


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setitem(CONFIG, "auth_token", "")
    monkeypatch.setattr(P, "PROJECTS_DIR", tmp_path)   # 對帳不去翻真的對話紀錄
    P.PERMS.clear()
    A.ASKS.clear()
    RUNS.clear()
    BY_SESSION.clear()
    yield TestClient(A.app)
    P.PERMS.clear()
    A.ASKS.clear()
    RUNS.clear()
    BY_SESSION.clear()


def _open_desktop_perm(client, sid="abcdef01-2222-3333-4444-555555555555", cmd="rm -rf build"):
    r = client.post("/api/perm", json={"session_id": sid, "tool_name": "Bash", "tool_input": {"command": cmd},
                                       "event": "PermissionRequest"})
    assert r.status_code == 200
    return r.json()["perm_id"]


def test_desktop_perm_card_is_persisted_and_restored_after_restart(client):
    pid = _open_desktop_perm(client)
    rows = S.pending_open(P.PERM_TTL)
    assert [x["id"] for x in rows] == [pid] and rows[0]["kind"] == "perm" and rows[0]["run_id"] is None
    assert "checked" not in rows[0]["payload"] and rows[0]["payload"]["tool"] == "Bash"

    P.PERMS.clear()                      # 伺服器重啟：記憶體表空了
    assert P.restore_perms() == 1
    assert P.PERMS[pid]["tool"] == "Bash" and P.PERMS[pid]["answer"] is None
    assert P.restore_perms() == 0        # 第二次不會重複載
    # 載回來的卡在統一入口看得到；手機答了會落地
    assert [c["id"] for c in client.get("/api/pending").json()["items"]] == [pid]
    assert client.post(f"/api/perm/{pid}/answer", json={"decision": "deny"}).json()["ok"] is True
    assert S.pending_open(P.PERM_TTL) == []


def test_phone_run_cards_are_not_restored_but_desktop_ones_are(client):
    S.pending_put("phone-perm", "perm", "run-x", "sid", {"tool": "Bash", "detail": "", "preview": "", "reason": ""})
    S.pending_put("desk-perm", "perm", None, "sid", {"tool": "Bash", "detail": "", "preview": "", "reason": ""})
    S.pending_put("ask-1", "ask", "run-x", "sid", {"questions": []})
    assert P.restore_perms() == 1 and list(P.PERMS) == ["desk-perm"]


def test_pending_endpoint_flags_cards_whose_waiter_stopped_polling(client):
    pid = _open_desktop_perm(client)
    p = P.PERMS[pid]
    p["created"] = time.time() - 100                      # 建立很久、從沒人來問
    d = client.get("/api/pending").json()
    assert d["n_open"] == 0 and d["n_gone"] == 1 and d["items"][0]["waiter_gone"] is True
    assert client.get("/api/rooms").json()["pending_perms"] == []   # 橫幅不列它
    p["last_poll"] = time.time()                          # hook 剛來輪詢過
    d = client.get("/api/pending").json()
    assert d["n_open"] == 1 and d["items"][0]["waiter_gone"] is False
    assert [c["id"] for c in client.get("/api/rooms").json()["pending_perms"]] == [pid]


def test_pending_endpoint_lists_phone_asks_with_their_room(client):
    run = A.Run("run-1", "slug", "sid-phone", "C:/x")
    A.register(run, "claude")
    r = client.post("/api/ask", json={"run_id": "run-1", "tool_input": {"questions": [{"question": "要推嗎"}]}})
    aid = r.json()["ask_id"]
    A.ASKS[aid]["last_poll"] = time.time()
    items = client.get("/api/pending").json()["items"]
    assert [(c["kind"], c["sid"], c["tool"], c["run_id"]) for c in items] == [("ask", "sid-phone", "AskUserQuestion", "run-1")]
    assert [c["id"] for c in client.get("/api/rooms").json()["pending_perms"]] == [aid]
    # run 結束後就不列
    asyncio.run(_emit(run, {"kind": "done", "ok": True}))
    assert client.get("/api/pending").json()["items"] == []


def test_send_with_client_msg_id_runs_once_and_rejects_conflicts(client, monkeypatch, tmp_path):
    started = []

    async def fake_run_claude(run, text, mode, extra):
        started.append(text)
        await _emit(run, {"kind": "done", "ok": True, "sid": run.sid})

    monkeypatch.setattr(A, "run_claude", fake_run_claude)
    body = {"text": "推上去", "project": str(tmp_path), "engine": "claude", "client_msg_id": "m-1"}
    r1 = client.post("/api/send", json=body).json()
    r2 = client.post("/api/send", json=body).json()
    assert r2["run_id"] == r1["run_id"] and r2["dedup"] is True
    assert started == ["推上去"] and len(RUNS) == 1
    r3 = client.post("/api/send", json=dict(body, text="不要推"))
    assert r3.status_code == 409
    # 建 run 之前就失敗的送出會放掉編號：改好再送不算衝突
    bad = client.post("/api/send", json={"text": "x", "project": str(tmp_path), "engine": "nope", "client_msg_id": "m-2"})
    assert bad.status_code == 400
    ok = client.post("/api/send", json={"text": "x", "project": str(tmp_path), "engine": "claude", "client_msg_id": "m-2"})
    assert ok.status_code == 200 and len(started) == 2
