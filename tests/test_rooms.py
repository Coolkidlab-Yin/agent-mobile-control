"""聊天室清單：可見／隱藏規則、compact 世代合併、路徑參數防跳目錄。所有磁碟位置都指到暫存目錄。"""
import time

import pytest
from fastapi import HTTPException
from helpers import assistant, write_jsonl

from claude_chat import perm as P
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
    monkeypatch.setattr(R, "open_perms", lambda *a, **k: [])
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


def test_scan_rooms_links_desktop_child_sessions_to_their_parent(sandbox, monkeypatch):
    mk_room(sandbox, "C--work-p1", SID_A, "claude-desktop", "父")
    mk_room(sandbox, "C--work-p1", SID_B, "claude-desktop", "子")
    reg = {SID_A: {"local_id": "local_a", "title": "父對話", "archived": False, "cwd": "C:/work/C--work-p1",
                   "last": 0, "parent": "", "kind": ""},
           SID_B: {"local_id": "local_b", "title": "父對話 (fork)", "archived": False, "cwd": "C:/work/C--work-p1",
                   "last": 0, "parent": "local_a", "kind": "fork"}}
    monkeypatch.setattr(R, "desktop_registry", lambda: reg)
    rooms = {r["sid"]: r for r in R.scan_rooms()}
    assert (rooms[SID_B]["parent_sid"], rooms[SID_B]["parent_title"], rooms[SID_B]["child_kind"]) == (SID_A, "父對話", "fork")
    assert "parent_sid" not in rooms[SID_A] and rooms[SID_A]["child_kind"] == "" and "_local" not in rooms[SID_A]
    reg[SID_A] = dict(reg[SID_A], archived=True)   # 父對話封存了：子對話還在，只留父對話標題
    R._room_cache.clear()
    rooms = {r["sid"]: r for r in R.scan_rooms()}
    assert SID_A not in rooms and (rooms[SID_B]["parent_sid"], rooms[SID_B]["parent_title"]) == ("", "父對話")


def mk_named(proj, slug, sid, name):
    recs = [{"type": "custom-title", "customTitle": name, "sessionId": sid},
            {"type": "user", "cwd": "C:/work/" + slug, "entrypoint": "claude-desktop",
             "message": {"role": "user", "content": "開場"}, "timestamp": "2026-10-04T01:00:00Z"}]
    recs += [assistant("回覆 %d，多放幾個字讓檔案超過兩百位元組的門檻" % i) for i in range(3)]
    return write_jsonl(proj / slug / (sid + ".jsonl"), recs)


def test_desktop_rename_wins_over_the_name_at_the_top_of_the_file(sandbox, monkeypatch):
    mk_named(sandbox, "C--work-p1", SID_A, "開對話時的名字")
    reg = {SID_A: {"local_id": "local_a", "title": "桌面自動取的名字", "archived": False, "cwd": "C:/work/C--work-p1",
                   "last": 0, "renamed": False}}
    monkeypatch.setattr(R, "desktop_registry", lambda: reg)
    assert R.scan_rooms()[0]["title"] == "開對話時的名字"   # 沒改過名：紀錄檔裡的名字（手機開的、--name 帶的）優先
    reg[SID_A] = dict(reg[SID_A], title="使用者改的名字", renamed=True)
    R._room_cache.clear()
    assert R.scan_rooms()[0]["title"] == "使用者改的名字"


def test_older_segment_left_by_desktop_clear_folds_into_the_same_room(sandbox, monkeypatch):
    """桌面對話 /clear 後登錄檔只指新紀錄檔；舊那段靠記下來的對照併回同一間，不再頂著交接指令當名字。"""
    mk_room(sandbox, "C--work-p1", SID_A, "claude-desktop", "交接後這段")
    mk_room(sandbox, "C--work-p1", SID_B, "claude-desktop", "【自動交接】上一段對話")
    R.remember_app_sid(SID_B)   # 手機碰過的舊段落才會被列出來
    reg = {SID_A: {"local_id": "local_a", "title": "週報草稿", "archived": False, "cwd": "C:/work/C--work-p1",
                   "last": 0, "renamed": True}}
    monkeypatch.setattr(R, "desktop_registry", lambda: reg)
    monkeypatch.setattr(R, "desktop_gens", lambda: {})
    assert len(R.scan_rooms()) == 2
    monkeypatch.setattr(R, "desktop_gens", lambda: {SID_A: "local_a", SID_B: "local_a"})
    R._room_cache.clear()
    rooms = R.scan_rooms()
    assert [r["title"] for r in rooms] == ["週報草稿"] and set(rooms[0]["gen_sids"]) == {SID_A, SID_B}


def test_phone_rename_holds_until_the_desktop_renames_again(sandbox, monkeypatch):
    mk_room(sandbox, "C--work-p1", SID_A, "claude-desktop", "開場")
    reg = {SID_A: {"local_id": "local_a", "title": "桌面的名字", "archived": False, "cwd": "C:/work/C--work-p1",
                   "last": 0, "renamed": True}}
    monkeypatch.setattr(R, "desktop_registry", lambda: reg)
    R.save_title(SID_A, "手機改的", "桌面的名字")
    assert R.scan_rooms()[0]["title"] == "手機改的"
    reg[SID_A] = dict(reg[SID_A], title="桌面後來又改")   # 最後改的贏
    R._room_cache.clear()
    assert R.scan_rooms()[0]["title"] == "桌面後來又改"


def test_room_flags_ignore_cards_whose_hook_is_gone(sandbox, monkeypatch):
    """清單上的「等你授權」只看 hook 還活著的卡：伺服器重啟、桌面對話關了的卡按了也沒人收（11-05）。"""
    mk_room(sandbox, "C--work-p1", SID_A, "claude-desktop", "甲")
    mk_room(sandbox, "C--work-p1", SID_B, "claude-desktop", "乙")
    monkeypatch.setattr(R, "open_perms", P.open_perms)
    monkeypatch.setattr(P, "_desktop_answered", lambda p: None)
    now = time.time()
    base = {"tool": "Bash", "detail": "", "preview": "", "reason": "", "answer": None, "by": "", "run_id": None}
    P.PERMS.clear()
    P.PERMS["alive"] = dict(base, sid=SID_A, created=now - 7200, last_poll=now - 3)
    P.PERMS["gone"] = dict(base, sid=SID_B, created=now - 7200, last_poll=now - 300)
    try:
        rooms = {r["sid"]: r for r in R.scan_rooms()}
        assert rooms[SID_A]["perm"] is True and rooms[SID_B]["perm"] is False
    finally:
        P.PERMS.clear()
