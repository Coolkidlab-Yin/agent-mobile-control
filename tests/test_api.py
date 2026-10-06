"""路由層：驗證、授權、檔案開放範圍、前端 JS 組裝。TestClient 不用 with，所以不會觸發 startup（不去登記 peer pipe）。"""
import pytest
from fastapi.testclient import TestClient

from claude_chat import api as A
from claude_chat.config import CONFIG


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(CONFIG, "auth_token", "")
    return TestClient(A.app)


def test_home_endpoint_returns_cards_counts_and_pending_perms(client, monkeypatch):
    fake_rooms = [{"sid": "h1", "slug": "p", "title": "A", "project_name": "P", "last_epoch": 1.0, "preview": "",
                   "running": False, "busy": False}]
    monkeypatch.setattr(A, "scan_rooms", lambda show_all=False: fake_rooms)
    monkeypatch.setattr(A, "pending_cards", lambda sid=None: [
        {"sid": "h1", "tool": "AskUserQuestion", "waiter_gone": False, "questions": [{"question": "Q"}], "created": 1}])
    d = client.get("/api/home").json()
    assert set(d) == {"cards", "counts", "pending_perms", "synced"}
    assert d["cards"][0]["section"] == "todo" and d["cards"][0]["note"] == "Q"
    assert d["counts"] == {"todo": 1, "running": 0, "done": 0}
    assert d["pending_perms"][0]["sid"] == "h1"


def test_observe_endpoint_records_turns_and_rejects_unknown_kinds(client, monkeypatch):
    from claude_chat import peer as PEER
    monkeypatch.setattr(PEER, "OBS", {})
    assert client.post("/api/observe", json={"sid": "s1", "kind": "bogus"}).status_code == 400
    assert client.post("/api/observe", json={"sid": " ", "kind": "turn.start"}).status_code == 400
    r = client.post("/api/observe", json={"sid": "s1", "kind": "turn.start", "ts": 5.0, "turnId": "t1"})
    assert r.status_code == 200 and r.json() == {"ok": True, "matched": False}
    obs = client.get("/api/status").json()["observed"]
    assert obs["s1"]["executing"] is True and obs["s1"]["turn_id"] == "t1" and obs["s1"]["since"] == 5.0
    client.post("/api/observe", json={"sid": "s1", "kind": "turn.complete", "turnId": "t1", "reason": "answer"})
    assert client.get("/api/status").json()["observed"]["s1"]["executing"] is False


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True and "perm_ready" in r.json()


def test_auth_token_guards_non_local_clients(monkeypatch):
    monkeypatch.setitem(CONFIG, "auth_token", "s3cret")
    c = TestClient(A.app)   # client host 是 "testclient"，不算本機
    assert c.get("/api/health").status_code == 401
    assert c.get("/api/health", headers={"x-auth-token": "s3cret"}).status_code == 200
    assert c.get("/api/health?token=s3cret").status_code == 200


def test_file_endpoint_enforces_roots_and_downloads_active_content(client, tmp_path, monkeypatch):
    assert client.get("/api/file", params={"path": "relative.txt"}).status_code == 400
    assert client.get("/api/file", params={"path": r"C:\Windows\win.ini"}).status_code == 403
    monkeypatch.setattr(A, "ALLOWED_FILE_ROOTS", (str(tmp_path.resolve()).casefold() + "\\",))
    (tmp_path / "page.html").write_text("<script>alert(1)</script>")
    (tmp_path / "note.txt").write_text("hi")
    r = client.get("/api/file", params={"path": str(tmp_path / "page.html")})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]   # 不讓它以本服務身分執行
    r = client.get("/api/file", params={"path": str(tmp_path / "note.txt")})
    assert r.status_code == 200 and "content-disposition" not in r.headers and r.text == "hi"


def test_bad_room_params_are_rejected(client):
    assert client.get("/api/history/bad slug/x").status_code == 400
    assert client.get("/api/bg/ok/a b").status_code == 400


def test_send_validates_before_spawning(client):
    assert client.post("/api/send", json={"text": "  "}).status_code == 400
    assert client.post("/api/send", json={"text": "hi", "mode": "yolo"}).status_code == 400
    r = client.post("/api/send", json={"text": "hi", "mode": "plan"})
    assert r.status_code == 400 and "專案" in r.json()["detail"]


def test_app_js_is_assembled_in_order_with_etag(client):
    r = client.get("/static/app.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/javascript")
    body = r.text
    marks = ["function esc(", "const MODEL_LIST", "function renderRooms(", "function openRoom(", "function buildAskCard(",
             "async function renderKeys(", "function openFile(", "function renderBg(", "roomsTimer = setInterval("]
    pos = [body.index(m) for m in marks]
    assert pos == sorted(pos), "static/js 的接法順序跑掉了"
    assert body.count('"use strict";') == len(A.JS_PARTS)
    assert client.get("/static/app.js", headers={"if-none-match": r.headers["etag"]}).status_code == 304


def test_status_and_rooms_shapes(client):
    assert set(client.get("/api/status").json()) == {"running", "observed"}
    r = client.get("/api/rooms")
    assert r.status_code == 200 and set(r.json()) == {"rooms", "pending_perms"}


def test_history_returns_pending_and_answered_desktop_cards(client, tmp_path, monkeypatch):
    """10-06：房間有即時連線時手機只從 n_events 之後接事件，早就開著的卡要靠 history 補回來，
    不然只看得到轉圈圈的 AskUserQuestion、沒有卡。"""
    import time as _t

    from helpers import assistant, tool_use, write_jsonl

    from claude_chat import perm as P
    from claude_chat import rooms as R
    sid = "dddddddd-0000-0000-0000-000000000006"
    monkeypatch.setattr(R, "PROJECTS_DIR", tmp_path)
    write_jsonl(tmp_path / "slug" / (sid + ".jsonl"),
                [assistant([tool_use("AskUserQuestion", {"questions": [{"question": "推不推？"}]}, "u1")])])
    P.PERMS.clear()
    P.PERMS["p1"] = {"run_id": None, "sid": sid, "tool": "AskUserQuestion", "detail": "推不推？", "preview": "",
                     "reason": "", "answer": None, "by": "", "created": _t.time(), "command": "推不推？",
                     "questions": [{"question": "推不推？"}]}
    P.PERMS["p2"] = dict(P.PERMS["p1"], answer="allow", by="desktop")
    P.PERMS["p3"] = dict(P.PERMS["p1"], sid="other-session")        # 別的房的卡不混進來
    try:
        d = client.get("/api/history/slug/" + sid).json()
        assert [p["perm_id"] for p in d["pending"]] == ["p1"] and d["pending"][0]["questions"][0]["question"] == "推不推？"
        assert [p["perm_id"] for p in d["answered"]] == ["p2"] and d["answered"][0]["by"] == "desktop"
    finally:
        P.PERMS.clear()


def test_perm_poll_tells_hook_to_quit_once_desktop_answered(client, tmp_path, monkeypatch):
    """10-06：桌面先答了之後 hook 還會輪詢到逾時——hook 來查時也要對 jsonl，答了就回 ask 讓它退出。"""
    import time as _t

    from helpers import assistant, tool_result, tool_use, write_jsonl

    from claude_chat import perm as P
    sid = "eeeeeeee-0000-0000-0000-000000000007"
    monkeypatch.setattr(P, "PROJECTS_DIR", tmp_path)
    write_jsonl(tmp_path / "slug" / (sid + ".jsonl"), [
        assistant([tool_use("AskUserQuestion", {"questions": [{"question": "推不推？"}]}, "u1")]),
        tool_result("u1", "Your questions have been answered: ..."),
    ])
    P.PERMS.clear()
    P.PERMS["p9"] = {"run_id": None, "sid": sid, "tool": "AskUserQuestion", "detail": "推不推？", "preview": "",
                     "reason": "", "answer": None, "by": "", "created": _t.time() - 5, "command": "推不推？",
                     "questions": [{"question": "推不推？"}]}
    try:
        d = client.get("/api/perm/p9").json()
        assert d == {"decision": "ask", "by": "desktop"}
        assert P.PERMS["p9"]["answer"] == "allow" and P.PERMS["p9"]["by"] == "desktop"
        # 手機那邊看到的是「已回答（桌面）」
        assert [p["perm_id"] for p in P.pending_perms(sid, answered=True)] == ["p9"]
    finally:
        P.PERMS.clear()
