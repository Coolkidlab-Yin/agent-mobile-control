"""工作台分段（claude_chat/home.py）：純函式，資料用參數餵；時間固定，不真等。"""
from claude_chat import home as H

NOW = 1_800_000_000.0
ROOM = {"sid": "s1", "slug": "p", "title": "t", "project_name": "P", "last_epoch": NOW - 100, "preview": "最後一句"}


def cls(room=None, pending=(), last_run=None, obs=None):
    return H.classify(dict(ROOM, **(room or {})), list(pending), last_run, obs, NOW)


def test_open_question_beats_everything_and_carries_the_question():
    ask = {"tool": "AskUserQuestion", "waiter_gone": False, "questions": [{"question": "要推嗎？"}]}
    got = cls(room={"running": True}, pending=[ask], last_run={"ok": 0, "ended": NOW - 10})
    assert got == ("todo", "等你回答", "ask", "要推嗎？")


def test_desktop_ask_card_reads_question_from_tool_input():
    ask = {"tool": "AskUserQuestion", "waiter_gone": False, "input": {"questions": [{"question": "哪個？"}]}}
    assert cls(pending=[ask])[3] == "哪個？"


def test_permission_card_summarises_the_command():
    perm = {"tool": "Bash", "waiter_gone": False, "input": {"command": "rm -rf build"}}
    assert cls(pending=[perm]) == ("todo", "等你授權", "perm", "rm -rf build")
    assert cls(pending=[{"tool": "Edit", "waiter_gone": False}]) == ("todo", "等你授權", "perm", "Edit")


def test_cards_whose_waiter_is_gone_do_not_count():
    ask = {"tool": "AskUserQuestion", "waiter_gone": True, "questions": [{"question": "?"}]}
    assert cls(pending=[ask]) == ("done", "", "none", "")


def test_failed_and_unknown_runs_go_to_todo_within_the_window_only():
    failed = {"status": "failed", "ok": 0, "ended": NOW - 3600, "error": "boom"}
    assert cls(last_run=failed) == ("todo", "出了問題", "failed", "boom")
    assert cls(last_run={"status": "unknown", "ok": None, "ended": NOW - 3600})[:3] == ("todo", "結果不明", "unknown")
    old = NOW - H.FAILED_WINDOW - 1
    assert cls(last_run={"status": "failed", "ok": 0, "ended": old, "started": old}) == ("done", "", "none", "")


def test_running_signals_phone_run_observer_and_recent_write():
    assert cls(room={"running": True}) == ("running", "執行中", "running", "手機起的工作進行中")
    assert cls(obs={"executing": True}) == ("running", "執行中", "running", "桌面對話正在執行")
    assert cls(room={"busy": True}) == ("running", "執行中", "running", "桌面剛剛還在寫入紀錄")
    assert cls(obs={"executing": False}) == ("done", "", "none", "")


def test_done_badge_only_for_a_recent_successful_run():
    assert cls(last_run={"status": "ended", "ok": 1, "ended": NOW - 60}) == ("done", "已完成", "done", "")
    assert cls(last_run={"status": "ended", "ok": 1, "ended": NOW - H.DONE_WINDOW - 1}) == ("done", "", "none", "")


def test_build_matches_pending_and_runs_across_merged_generations_and_counts():
    rooms = [dict(ROOM), dict(ROOM, sid="s2", gen_sids=["s2", "s2old"]), dict(ROOM, sid="s3")]
    pending = [{"sid": "s2old", "tool": "AskUserQuestion", "waiter_gone": False, "questions": [{"question": "舊世代的卡"}]}]
    runs = {"s3": {"run_id": "r", "status": "ended", "ok": 1, "ended": NOW - 5, "started": NOW - 50, "error": None}}
    out = H.build(rooms, pending, lambda sid, within: runs.get(sid),
                  lambda sid: {"executing": True} if sid == "s1" else None, NOW)
    by = {c["sid"]: c for c in out["cards"]}
    assert by["s1"]["section"] == "running" and by["s1"]["executing"] is True
    assert by["s2"]["section"] == "todo" and by["s2"]["note"] == "舊世代的卡" and by["s2"]["n_pending"] == 1
    assert by["s3"]["section"] == "done" and by["s3"]["badge"] == "已完成"
    assert by["s3"]["last_run"] == {"status": "ended", "ok": 1, "ended": NOW - 5, "error": None}
    assert out["counts"] == {"todo": 1, "running": 1, "done": 1}
    assert by["s1"]["preview"] == "最後一句"   # room 欄位原樣帶著，清單頁照舊能用
