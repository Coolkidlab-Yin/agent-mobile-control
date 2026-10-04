"""聊天室清單：可見／隱藏規則、compact 世代合併、路徑參數防跳目錄。所有磁碟位置都指到暫存目錄。"""
import pytest
from fastapi import HTTPException
from helpers import assistant, write_jsonl

from claude_chat import rooms as R

SID_A = "aaaaaaaa-0000-0000-0000-000000000001"
SID_B = "bbbbbbbb-0000-0000-0000-000000000002"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    proj = tmp_path / "projects"
    proj.mkdir()
    for name in ("LIVE_DIR", "APP_SESSIONS", "WEB_ARCHIVE", "TITLES_FILE", "SNAP_FILE", "CODEX_SESSIONS", "API_CHATS"):
        monkeypatch.setattr(R, name, tmp_path / name.lower())
    monkeypatch.setattr(R, "PROJECTS_DIR", proj)
    monkeypatch.setattr(R, "desktop_registry", lambda: {})
    monkeypatch.setattr(R, "pending_perms", lambda *a, **k: [])
    R._room_cache.clear()
    for cache in (R._app_sids_cache, R._titles_cache, R._overlay_cache, R._snap_cache):
        cache["mtime"] = None
    return proj


def mk_room(proj, slug, sid, entry, title="標題"):
    recs = [{"type": "user", "cwd": "C:\\work\\" + slug, "entrypoint": entry,
             "message": {"role": "user", "content": title}, "timestamp": "2026-10-04T01:00:00Z"}]
    recs += [assistant("回覆 %d，多放幾個字讓檔案超過兩百位元組的門檻" % i) for i in range(3)]
    return write_jsonl(proj / slug / (sid + ".jsonl"), recs)


def test_scan_rooms_hides_bots_unless_app_opened_and_honors_overlay(sandbox):
    mk_room(sandbox, "C--work-p1", SID_A, "cli", "手動開的")
    mk_room(sandbox, "C--work-p1", SID_B, "sdk-cli", "排程機器人")
    assert [r["sid"] for r in R.scan_rooms()] == [SID_A]
    allr = {r["sid"]: r for r in R.scan_rooms(show_all=True)}
    assert allr[SID_B]["hidden"] is True and allr[SID_A]["hidden"] is False
    # 手機開的對話 entrypoint 也是 sdk-cli，靠名單救回來
    R.remember_app_sid(SID_B)
    assert {r["sid"] for r in R.scan_rooms()} == {SID_A, SID_B}
    assert next(r for r in R.scan_rooms() if r["sid"] == SID_B)["app"] is True
    # 網頁端封存會收起來；改名會蓋掉標題
    R.save_overlay({SID_A: "archived"})
    R.save_title(SID_B, "我改的名字")
    assert [(r["sid"], r["title"]) for r in R.scan_rooms()] == [(SID_B, "我改的名字")]
    assert next(r for r in R.scan_rooms(show_all=True) if r["sid"] == SID_A)["archived"] is True


def test_merge_compact_generations_keeps_newest_or_live():
    gens = [
        {"sid": "old", "custom_title": "同一場", "project": "C:\\p", "last_epoch": 100},
        {"sid": "new", "custom_title": "同一場", "project": "c:/p/", "last_epoch": 200},
        {"sid": "solo", "custom_title": "", "project": "C:\\p", "last_epoch": 300},
    ]
    out = {r["sid"]: r for r in R._merge_compact_generations(gens)}
    assert set(out) == {"new", "solo"} and out["new"]["generations"] == 2 and set(out["new"]["gen_sids"]) == {"old", "new"}
    # 桌面正開著舊世代 → 以它為代表（訊息才直送得進去）
    assert {r["sid"] for r in R._merge_compact_generations(gens, live={"old"})} == {"old", "solo"}


def test_find_room_file_rejects_traversal_and_missing(sandbox):
    for slug, sid in [("..", "x"), ("a/b", "x"), ("ok", "../../etc"), ("ok", "a b")]:
        with pytest.raises(HTTPException) as e:
            R.find_room_file(slug, sid)
        assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        R.find_room_file("ok", "nope")
    assert e.value.status_code == 404
    mk_room(sandbox, "ok", SID_A, "cli")
    assert R.find_room_file("ok", SID_A).name == SID_A + ".jsonl"
