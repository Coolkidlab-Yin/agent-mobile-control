"""歷史訊息與尾讀：手機進房看到的泡泡、工具勾叉、分頁，都從這裡來。"""
from helpers import assistant, tool_result, tool_use, user, write_jsonl

from claude_chat import history as H


def test_load_history_maps_tool_results_and_compact_summary(tmp_path):
    p = write_jsonl(tmp_path / "h.jsonl", [
        user("幫我跑"),
        assistant([tool_use("Bash", {"command": "pytest"}, "u1")]),
        tool_result("u1", "boom", is_error=True),
        assistant("掛了"),
        dict(user("前情提要……"), isCompactSummary=True),
    ])
    out = H.load_history(p)
    assert [(i["kind"], i.get("ok"), i.get("label")) for i in out["items"]] == [
        ("text", None, None), ("tool", False, None), ("text", None, None), ("info", None, "前情摘要")]
    assert out["more"] is False and out["oldest"] == 0 and out["size"] == p.stat().st_size and out["context"] is None


def _boundary(trigger="manual", pre=407138, post=68671):
    # Claude Code 2.1.283 起 /compact 的唯一痕跡（真實紀錄照抄欄位，不再有 isCompactSummary 的 user 條目）
    return {"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted",
            "compactMetadata": {"trigger": trigger, "preTokens": pre, "postTokens": post, "durationMs": 938},
            "timestamp": "2026-10-06T06:23:45.721Z"}


def test_compact_boundary_shows_on_phone_in_both_paths(tmp_path):
    """手機從兩條路看對話都要看到「對話已壓縮」卡：進房載歷史，以及旁觀桌面時尾讀。
    其他 system 紀錄（例如 stop_hook_summary）照舊不顯示。"""
    p = write_jsonl(tmp_path / "c.jsonl", [
        user("先做"), _boundary(), {"type": "system", "subtype": "stop_hook_summary", "content": "x"},
        assistant("接著做"),
    ])
    items = H.load_history(p)["items"]
    assert [(i["kind"], i.get("label")) for i in items] == [("text", None), ("info", "對話已壓縮"), ("text", None)]
    assert items[1]["text"] == "手動壓縮：前文 407k tokens 收成 69k tokens" and items[1]["ts"].startswith("2026-10-06")
    tail, _ = H.tail_items(p, 0)
    assert [(i["kind"], i.get("label")) for i in tail if i["kind"] != "tool_ok"] == \
        [("text", None), ("info", "對話已壓縮"), ("text", None)]
    auto = H.load_history(write_jsonl(tmp_path / "a.jsonl", [_boundary("auto", 500, 20)]))["items"]
    assert auto[0]["text"] == "自動壓縮：前文 500 tokens 收成 20 tokens"


def test_load_history_pages_backwards(tmp_path):
    p = write_jsonl(tmp_path / "p.jsonl", [user(f"第 {i} 句") for i in range(5)])
    page = H.load_history(p, limit=2)
    assert [i["text"] for i in page["items"]] == ["第 3 句", "第 4 句"] and page["more"] is True and page["oldest"] == 3
    older = H.load_history(p, before=page["oldest"], limit=2)
    assert [i["text"] for i in older["items"]] == ["第 1 句", "第 2 句"]


def test_tail_items_only_consumes_complete_lines(tmp_path):
    p = write_jsonl(tmp_path / "t.jsonl", [assistant([tool_use("Read", {"file_path": "a.py"}, "r1")])])
    items, off = H.tail_items(p, 0)
    assert [i["kind"] for i in items] == ["tool"] and off == p.stat().st_size
    with open(p, "a", encoding="utf-8") as f:   # 半行：桌面正在寫，還沒換行
        f.write('{"type":"user","message":{"role":"user","content":"<task-notification>done</task-notif')
    assert H.tail_items(p, off) == ([], off)
    with open(p, "a", encoding="utf-8") as f:
        f.write('ication>"}}\n')
    items, off2 = H.tail_items(p, off)
    assert [(i["kind"], i.get("label")) for i in items] == [("info", "背景工作回報")] and off2 == p.stat().st_size


def test_codex_and_api_history(tmp_path):
    cx = write_jsonl(tmp_path / "rollout.jsonl", [
        {"type": "session_meta", "payload": {"cwd": "C:\\x", "session_id": "th1"}},
        {"type": "event_msg", "payload": {"type": "task_started", "model_context_window": 1000}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "嗨"}, "timestamp": "T1"},
        {"type": "event_msg", "payload": {"type": "exec_command_begin", "command": ["ls", "-la"]}},
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "好"}},
        {"type": "event_msg", "payload": {"type": "token_count",
                                          "info": {"total_token_usage": {"input_tokens": 400, "cached_input_tokens": 100}}}},
    ])
    out = H.codex_history(cx)
    assert [i["kind"] for i in out["items"]] == ["text", "tool", "text"] and out["items"][1]["detail"] == "ls -la"
    assert out["context"] == {"tokens": 500, "window": 1000, "pct": 50}
    ap = write_jsonl(tmp_path / "api.jsonl", [{"role": "user", "content": "q"}, {"role": "assistant", "content": ""},
                                              {"role": "assistant", "content": "a"}])
    assert [i["text"] for i in H.api_history(ap)["items"]] == ["q", "a"]
