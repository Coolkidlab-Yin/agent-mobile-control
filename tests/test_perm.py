"""授權卡：桌面先按了要對得到（含對話在卡片開著期間暴長的情況）、過期卡要清掉。"""
import time

import pytest
from helpers import append_jsonl, assistant, tool_result, tool_use, write_jsonl

from claude_chat import perm as P

SID = "cccccccc-0000-0000-0000-000000000003"


@pytest.fixture(autouse=True)
def clean():
    P.PERMS.clear()
    yield
    P.PERMS.clear()


def test_perm_key_matches_bash_command_or_first_question():
    assert P._perm_key({"command": "rm -rf x"}) == "rm -rf x"
    assert P._perm_key({"questions": [{"question": "推不推？"}]}) == "推不推？"
    assert P._perm_key({"file_path": "a"}) is None and P._perm_key("x") is None


def test_desktop_answered_survives_file_growth_after_card(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "PROJECTS_DIR", tmp_path)
    f = write_jsonl(tmp_path / "slug" / (SID + ".jsonl"), [assistant([tool_use("Bash", {"command": "git push"}, "u9")])])
    p = {"sid": SID, "command": "git push"}
    assert P._desktop_answered(p) is None and p["scan_from"] == 0
    # 卡片開著期間對話又長了 1.5MB（10-01 的坑：固定只翻檔尾 256KB 會漏掉），答案要照樣找得到
    append_jsonl(f, [assistant("x" * 1000) for _ in range(1500)] + [tool_result("u9", "pushed")])
    assert f.stat().st_size > 1024 * 1024
    assert P._desktop_answered(p) == "allow"


def test_desktop_answered_recognizes_deny_and_missing_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "PROJECTS_DIR", tmp_path)
    write_jsonl(tmp_path / "s" / (SID + ".jsonl"), [assistant([tool_use("Bash", {"command": "rm x"}, "u1")]),
                                                   tool_result("u1", "User denied this command")])
    assert P._desktop_answered({"sid": SID, "command": "rm x"}) == "deny"
    assert P._desktop_answered({"sid": SID, "command": None}) is None
    assert P._desktop_answered({"sid": "no-such", "command": "x"}) is None


def test_pending_perms_lists_desktop_cards_and_expires_old_ones(monkeypatch):
    monkeypatch.setattr(P, "_desktop_answered", lambda p: None)
    now = time.time()
    base = {"tool": "Bash", "detail": "", "preview": "", "reason": "", "answer": None, "by": "", "created": now}
    P.PERMS["fresh"] = dict(base, run_id=None, sid="s1")
    P.PERMS["phone"] = dict(base, run_id="r1", sid="s1")      # 手機 run 自己的卡走事件流，不列
    P.PERMS["done"] = dict(base, run_id=None, sid="s2", answer="allow")
    P.PERMS["stale"] = dict(base, run_id=None, sid="s1", created=now - P.PERM_TTL - 1)
    assert [c["perm_id"] for c in P.pending_perms()] == ["fresh"]
    assert [c["perm_id"] for c in P.pending_perms(answered=True)] == ["done"]
    assert P.pending_perms("s2") == [] and "stale" not in P.PERMS
