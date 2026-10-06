"""run 登記表的落地：事件寫進 SQLite、done 收尾、重啟後孤兒 run 變「結果不明」。"""
import asyncio

import pytest

from claude_chat import store as S
from claude_chat.runs import BY_SESSION, RESTART_NOTE, RUNS, Run, _emit, register, restore_from_store


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    RUNS.clear()
    BY_SESSION.clear()


def test_register_and_emit_persist_events_and_close_run():
    run = Run("r1", "slug", "sid-a", "C:/x")
    register(run, "claude")
    assert RUNS["r1"] is run and BY_SESSION["sid-a"] == "r1"

    async def go():
        await _emit(run, {"kind": "init", "sid": "sid-a"})
        await _emit(run, {"kind": "text", "text": "hi"})
        await _emit(run, {"kind": "done", "ok": True, "sid": "sid-a", "error": ""})

    asyncio.run(go())
    assert run.done
    assert [e["kind"] for e in S.events_for("r1")] == ["init", "text", "done"]
    last = S.last_run_for("sid-a")
    assert last["status"] == "ended" and last["ok"] == 1 and last["n_events"] == 3


def test_failed_done_is_recorded_as_failed_with_error():
    run = Run("r2", "slug", "sid-b", "C:/x")
    register(run, "codex")
    asyncio.run(_emit(run, {"kind": "done", "ok": False, "error": "boom"}))
    last = S.last_run_for("sid-b")
    assert last["status"] == "failed" and last["ok"] == 0 and last["error"] == "boom"


def test_restore_marks_previous_boot_runs_unknown_without_resurrecting_them(monkeypatch):
    monkeypatch.setattr(S, "BOOT_ID", "old-boot")
    S.run_open("orphan", "sid-c", "slug", "C:/x", "peer")
    S.event_append("orphan", 0, {"kind": "init", "sid": "sid-c"})
    S.event_append("orphan", 1, {"kind": "text", "text": "halfway"})
    S.run_open("finished", "sid-d", "slug", "C:/x", "claude")
    S.run_close("finished", True)
    monkeypatch.setattr(S, "BOOT_ID", "new-boot")

    assert restore_from_store() == 1
    run = RUNS["orphan"]
    assert run.done and run.peer
    assert [e["kind"] for e in run.events] == ["init", "text", "done"]
    assert run.events[-1]["ok"] is None and run.events[-1]["unknown"] is True
    assert "sid-c" not in BY_SESSION          # 不算進行中
    assert "finished" not in RUNS             # 已收尾的不載
    last = S.last_run_for("sid-c")
    assert last["status"] == "unknown" and last["error"] == RESTART_NOTE and last["n_events"] == 3
    # 第二次啟動不會再補一次
    assert restore_from_store() == 0
