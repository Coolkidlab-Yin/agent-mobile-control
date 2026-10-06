"""SQLite 落地層：runs / run_events / pending / messages。每個測試用自己的 tmp 資料庫。"""
import time

import pytest

from claude_chat import store as S


@pytest.fixture(autouse=True)
def db(tmp_path):
    S.init(tmp_path / "state.sqlite")
    yield
    S.init(tmp_path / "state2.sqlite")   # 不讓下一個測試接到這顆


def test_run_lifecycle_and_events_replay_in_seq_order():
    S.run_open("r1", "sid-a", "slug", "C:/x", "claude")
    S.event_append("r1", 0, {"kind": "init"})
    S.event_append("r1", 2, {"kind": "text", "t": "later"})
    S.event_append("r1", 1, {"kind": "text", "t": "earlier"})
    S.event_append("r1", 1, {"kind": "dup"})   # 同 seq 第二次寫入要被忽略
    assert [e.get("t") or e["kind"] for e in S.events_for("r1")] == ["init", "earlier", "later"]
    S.run_close("r1", True)
    last = S.last_run_for("sid-a")
    assert last["status"] == "ended" and last["ok"] == 1 and last["n_events"] == 3


def test_run_close_maps_ok_none_to_unknown():
    S.run_open("r2", "sid-b", "slug", "C:/x", "peer")
    S.run_close("r2", None, error="伺服器重啟")
    last = S.last_run_for("sid-b")
    assert last["status"] == "unknown" and last["ok"] is None and last["error"] == "伺服器重啟"


def test_orphan_runs_are_only_those_from_other_boots(monkeypatch):
    S.run_open("mine", "s1", "slug", "C:/x", "claude")
    monkeypatch.setattr(S, "BOOT_ID", "older-boot")
    S.run_open("theirs-open", "s2", "slug", "C:/x", "claude")
    S.run_open("theirs-done", "s3", "slug", "C:/x", "claude")
    S.run_close("theirs-done", True)
    monkeypatch.setattr(S, "BOOT_ID", "this-boot")
    assert [r["run_id"] for r in S.runs_open_from_other_boots()] == ["mine", "theirs-open"]


def test_pending_roundtrip_heartbeat_and_expiry():
    S.pending_put("p1", "perm", None, "sid-a", {"tool": "Bash"})
    S.pending_put("a1", "ask", "r1", "sid-a", {"questions": []})
    assert S.pending_last_poll("p1") is None
    S.pending_touch("p1")
    assert S.pending_last_poll("p1") >= time.time() - 5
    S.pending_answer("a1", {"answers": {"q": "x"}}, "phone")
    opened = S.pending_open(max_age=3600)
    assert [p["id"] for p in opened] == ["p1"]
    assert opened[0]["payload"] == {"tool": "Bash"} and opened[0]["kind"] == "perm"
    S._q("UPDATE pending SET created=? WHERE id='p1'", (time.time() - 100,))
    assert S.pending_open(max_age=50) == []


def test_message_claim_dedups_same_and_rejects_conflict():
    assert S.message_claim("m1", "sid", "h1") == ("new", None)
    S.message_bind("m1", "run-9")
    assert S.message_claim("m1", "sid", "h1") == ("same", "run-9")
    assert S.message_claim("m1", "sid", "h2") == ("conflict", None)
    # 建 run 前失敗：放掉鍵，重送改過的內容不算衝突
    assert S.message_claim("m2", "sid", "h1") == ("new", None)
    S.message_release("m2")
    assert S.message_claim("m2", "sid", "h3") == ("new", None)
    # 已綁 run 的鍵不能被 release 掉
    S.message_release("m1")
    assert S.message_claim("m1", "sid", "h1") == ("same", "run-9")


def test_prune_drops_old_rows_only(monkeypatch):
    S.run_open("old", "s", "slug", "C:/x", "claude")
    S.event_append("old", 0, {"kind": "init"})
    S._q("UPDATE runs SET started=? WHERE run_id='old'", (time.time() - 10 * 86400,))
    S.run_open("new", "s", "slug", "C:/x", "claude")
    S.prune(days=7)
    assert S.events_for("old") == [] and S.last_run_for("s")["run_id"] == "new"
