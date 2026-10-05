"""背景任務偵測：兩個實際踩過的誤判（引用的開工訊息、舊程序的任務）都要擋住；輸出檔只准讀 CLI 自己的暫存目錄。"""
import time

import pytest
from helpers import append_jsonl, assistant, iso, tool_result, tool_use, user, write_jsonl

from claude_chat import bgtasks as B

START = "Command running in background with ID: abc123\nOutput is being written to: C:\\tmp\\claude\\abc123.output"
NOTE = ("<task-notification><task-id>abc123</task-id><status>completed</status>"
        "<summary>跑完了</summary></task-notification>")


@pytest.fixture(autouse=True)
def clear_cache():
    B._BG_CACHE.clear()
    yield
    B._BG_CACHE.clear()


def test_bg_scan_tracks_start_completion_and_reads_incrementally(tmp_path):
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant([tool_use("Bash", {"command": "pytest", "description": "跑測試", "run_in_background": True}, "u1")]),
        tool_result("u1", START),
    ])
    st = B._bg_scan(p)
    t = st["tasks"]["abc123"]
    assert (t["kind"], t["label"], t["status"], t["out"]) == ("bash", "跑測試", "running", "C:\\tmp\\claude\\abc123.output")
    assert st["off"] == p.stat().st_size
    append_jsonl(p, [user(NOTE)])
    t = B._bg_scan(p)["tasks"]["abc123"]
    assert (t["status"], t["summary"]) == ("completed", "跑完了") and t["ended"] > 0
    # 檔案被換掉變短 → 從頭重讀
    write_jsonl(p, [user("新的開始")])
    assert B._bg_scan(p)["tasks"] == {}


def test_bg_start_message_quoted_mid_output_is_not_a_task(tmp_path):
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant([tool_use("Bash", {"command": "grep background log", "description": "找舊紀錄"}, "u1")]),
        tool_result("u1", "log 裡找到舊紀錄：\n" + START),
    ])
    assert B._bg_scan(p)["tasks"] == {}


def test_bg_notification_counts_only_from_system_record_types(tmp_path):
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant([tool_use("Bash", {"command": "x", "run_in_background": True}, "u1")]),
        tool_result("u1", START),
        tool_result("u2", NOTE),      # 工具結果裡引用通知文字：不算
        assistant(NOTE),               # 模型自己複述：不算
    ])
    assert B._bg_scan(p)["tasks"]["abc123"]["status"] == "running"
    append_jsonl(p, [{"type": "queue-operation", "operation": "enqueue", "content": NOTE}])
    assert B._bg_scan(p)["tasks"]["abc123"]["status"] == "completed"


def test_list_bg_tasks_status_depends_on_live_process_and_recency(tmp_path, monkeypatch):
    now = time.time()
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant([tool_use("Bash", {"command": "old", "run_in_background": True}, "u0")], ts=iso(now - 11 * 3600)),
        tool_result("u0", START.replace("abc123", "old1"), ts=iso(now - 11 * 3600)),
        user(NOTE.replace("abc123", "old1"), ts=iso(now - 10 * 3600)),
        assistant([tool_use("Bash", {"command": "cur", "run_in_background": True}, "u1")], ts=iso(now - 60)),
        tool_result("u1", START, ts=iso(now - 60)),
    ])
    monkeypatch.setattr(B, "_session_alive_since", lambda sid: None)
    # old1 結束超過 6 小時不列；abc123 沒有活著的程序可歸屬 → 狀態不明
    assert [(t["id"], t["status"]) for t in B.list_bg_tasks(p, "sid")["tasks"]] == [("abc123", "unknown")]
    monkeypatch.setattr(B, "_session_alive_since", lambda sid: now - 3600)
    assert B.list_bg_tasks(p, "sid")["tasks"][0]["status"] == "running"
    monkeypatch.setattr(B, "_session_alive_since", lambda sid: now)   # 程序比任務晚啟動 → 任務早跟著舊程序死了
    assert B.list_bg_tasks(p, "sid")["tasks"][0]["status"] == "unknown"


def test_bg_progress_only_reads_inside_cli_temp_dir(tmp_path):
    outside = tmp_path / "x.output"
    outside.write_text("secret")
    assert B._bg_progress({"kind": "bash", "out": str(outside), "dir": ""}) == (None, [])
    inside_dir = B._BG_TEMP_ROOT / "_test_bg"
    inside_dir.mkdir(parents=True, exist_ok=True)
    f = inside_dir / "t.output"
    f.write_text("\x1b[32mline1\x1b[0m\r\nline2\n", encoding="utf-8")
    try:
        last, tail = B._bg_progress({"kind": "bash", "out": str(f), "dir": ""})
        assert tail == ["line1", "line2"] and last == f.stat().st_mtime
    finally:
        f.unlink()
        inside_dir.rmdir()


def test_bg_task_stopped_by_taskstop_is_not_left_running(tmp_path):
    stopped = '{"message":"Successfully stopped task: abc123 (pytest)"}'
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant([tool_use("Bash", {"command": "pytest", "description": "跑測試"}, "u1")]),
        tool_result("u1", "Command did not complete within its 120s timeout and was moved to the background "
                          "(ID: abc123). Output is being written to: C:\\tmp\\claude\\abc123.output"),
        assistant([tool_use("Bash", {"command": "grep stopped log"}, "u2")]),
        tool_result("u2", "舊紀錄：\n" + stopped),                  # 只是被印出來：不算
        assistant([tool_use("TaskStop", {"task_id": "abc123"}, "u3")]),
        tool_result("u3", "<tool_use_error>No task found with ID: abc123</tool_use_error>", is_error=True),
    ])
    assert B._bg_scan(p)["tasks"]["abc123"]["status"] == "running"   # 停止失敗：不動
    append_jsonl(p, [assistant([tool_use("TaskStop", {"task_id": "abc123"}, "u4")]), tool_result("u4", stopped)])
    t = B._bg_scan(p)["tasks"]["abc123"]
    assert (t["status"], t["summary"]) == ("stopped", "手動停止") and t["ended"] > 0
