"""路由層：驗證、授權、檔案開放範圍、前端 JS 組裝。TestClient 不用 with，所以不會觸發 startup（不去登記 peer pipe）。"""
import pytest
from fastapi.testclient import TestClient

from claude_chat import api as A
from claude_chat.config import CONFIG


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(CONFIG, "auth_token", "")
    return TestClient(A.app)


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
    assert set(client.get("/api/status").json()) == {"running"}
    r = client.get("/api/rooms")
    assert r.status_code == 200 and set(r.json()) == {"rooms", "pending_perms"}
