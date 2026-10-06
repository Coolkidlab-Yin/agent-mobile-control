"""對話紀錄解析：這層一錯，清單標題、預覽、泡泡全部跟著錯。"""
import pytest
from helpers import assistant, tool_use, user, write_jsonl

from claude_chat import jsonl as J


def test_text_of_joins_text_blocks_only():
    assert J._text_of("hi") == "hi"
    assert J._text_of([{"type": "text", "text": "a"}, {"type": "tool_use"}, {"type": "text", "text": "b"}]) == "a\nb"
    assert J._text_of(None) == ""


def test_is_meta_user_filters_system_noise():
    assert J._is_meta_user({"isMeta": True}, "x")
    assert J._is_meta_user({}, "   ")
    assert J._is_meta_user({}, "<system-reminder>…")
    assert J._is_meta_user({}, "This session is being continued from…")
    assert not J._is_meta_user({}, "幫我看一下")


def test_items_from_message_normalizes_tools_and_sendfile():
    msg = {"content": [
        {"type": "text", "text": "先看"},
        tool_use("mcp__chat__ask_user", {"questions": []}, "t1"),
        tool_use("Bash", {"command": "ls\n-la", "description": "列檔"}, "t2"),
        tool_use("SendUserFile", {"files": ["C:\\out\\a.png", 3], "caption": " 成品 "}, "t3"),
    ]}
    items = J.items_from_message("assistant", msg, ts="T", tool_status={"t2": False})
    assert [(i["kind"], i.get("tool")) for i in items] == [
        ("text", None), ("tool", "ask_user"), ("tool", "Bash"), ("tool", "SendUserFile"), ("text", None)]
    assert items[2]["detail"] == "ls -la" and items[2]["ok"] is False and items[1]["ok"] is None
    assert items[-1]["text"] == "成品\nC:\\out\\a.png"   # 桌面的「傳檔案」卡片補成路徑文字，手機才看得到
    assert J.items_from_message("user", {"content": "  "}) == []


def test_head_info_prefers_custom_title_and_flags_compacted(tmp_path):
    p = write_jsonl(tmp_path / "a.jsonl", [
        {"type": "user", "cwd": "C:\\proj", "entrypoint": "cli",
         "message": {"role": "user", "content": "<local-command-stdout>x</local-command-stdout>"}},
        user("真正的第一句問題"),
        {"type": "custom-title", "customTitle": "  我的   標題  "},
        {"type": "user", "compactMetadata": {"trigger": "manual"}, "isCompactSummary": True,
         "message": {"role": "user", "content": "摘要"}},
    ])
    assert J._head_info(p) == {"cwd": "C:\\proj", "title": "我的 標題", "entry": "cli",
                               "custom_title": "我的 標題", "compacted": True}


def test_head_info_falls_back_to_queued_then_first_real_user(tmp_path):
    noise = user("<system-reminder>noise</system-reminder>")
    p = write_jsonl(tmp_path / "b.jsonl", [noise, user("第一句"), {"type": "queue-operation", "content": "排進來的那句"}])
    assert J._head_info(p)["title"] == "排進來的那句"
    p2 = write_jsonl(tmp_path / "c.jsonl", [noise, user("第一句")])
    assert J._head_info(p2)["title"] == "第一句"


def test_tail_info_takes_last_visible_message_but_newest_timestamp(tmp_path):
    p = write_jsonl(tmp_path / "t.jsonl", [
        assistant("舊回覆"),
        user("最後是我問的"),
        dict(assistant("旁支不算"), isSidechain=True, timestamp="2026-10-04T09:00:00Z"),
    ])
    assert J._tail_info(p) == {"preview": "你：最後是我問的", "ts": "2026-10-04T09:00:00Z", "role": "user", "tail": ""}
    # 最後一句是 Claude 說的：role=ai，tail 是原文結尾（工作台拿它判「等你回話」，不能是截過的 preview）
    p2 = write_jsonl(tmp_path / "t2.jsonl", [user("問"), assistant("改好了。\n要推嗎？")])
    t2 = J._tail_info(p2)
    assert t2["role"] == "ai" and t2["tail"] == "改好了。\n要推嗎？"


@pytest.mark.parametrize("cwd,slug,expected", [
    ("C:\\Users\\me\\Desktop\\brand", "x", "brand"),
    ("C:\\Users\\me\\proj\\.claude\\worktrees\\feat-1", "x", "proj"),   # worktree 顯示上層專案
    ("", "C--Users-me-Desktop-brand", "brand"),
    ("", "", "?"),
])
def test_project_name(cwd, slug, expected):
    assert J._project_name(cwd, slug) == expected


def test_small_helpers():
    assert J._slug_encode("C:\\a b/c") == "C--a-b-c"
    assert J._norm_cwd("c:/A/B/") == "c:\\a\\b"
    assert J._iso_epoch("not a date") == 0.0 and J._iso_epoch("") == 0.0
    assert J._iso_epoch("1970-01-01T00:00:10Z") == 10.0
    assert len(J._clean_title(" x " * 100)) == 64
    assert J._ctx_window("claude-fable-5-1", 10) == 1_000_000
    assert J._ctx_window("claude-opus-5", 200_000) == 1_000_000
    assert J._ctx_window("claude-opus-5", 10) == 200_000
