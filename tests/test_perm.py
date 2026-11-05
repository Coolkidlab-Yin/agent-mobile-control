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


def test_card_waiting_for_hours_stays_listed_while_its_hook_keeps_polling(monkeypatch):
    """桌面確認框沒有時限：hook 還在輪詢的卡開了幾小時也要列（11-05：55 分鐘逾時後桌面還在問、手機沒卡）。
    hook 已經不在的卡只標 waiter_gone 不列進 open_perms；答過的卡一小時後清掉。"""
    monkeypatch.setattr(P, "_desktop_answered", lambda p: None)
    now = time.time()
    base = {"tool": "Bash", "detail": "", "preview": "", "reason": "", "answer": None, "by": "", "run_id": None, "sid": "s1"}
    P.PERMS["old-alive"] = dict(base, created=now - 3 * 3600, last_poll=now - 5)
    P.PERMS["old-dead"] = dict(base, created=now - 3 * 3600, last_poll=now - 120)
    P.PERMS["answered-long-ago"] = dict(base, created=now - 3 * 3600, answer="allow", answered_at=now - 2 * 3600)
    P.PERMS["answered-just-now"] = dict(base, created=now - 3 * 3600, answer="allow", answered_at=now - 60)
    assert {c["perm_id"]: c["waiter_gone"] for c in P.pending_perms()} == {"old-alive": False, "old-dead": True}
    assert [c["perm_id"] for c in P.open_perms()] == ["old-alive"]
    assert [c["perm_id"] for c in P.pending_perms(answered=True)] == ["answered-just-now"]
    assert "answered-long-ago" not in P.PERMS


def test_desktop_answered_skips_rereading_an_unchanged_transcript(tmp_path, monkeypatch):
    """卡片可以開一整天：紀錄檔沒動過就不重讀（原本每 2 秒翻 1MB）；檔案長了才再翻，答案照樣找得到。"""
    monkeypatch.setattr(P, "PROJECTS_DIR", tmp_path)
    f = write_jsonl(tmp_path / "slug" / (SID + ".jsonl"), [assistant([tool_use("Bash", {"command": "git push"}, "u9")])])
    reads = []
    real = P._loads
    monkeypatch.setattr(P, "_loads", lambda line: reads.append(1) or real(line))
    p = {"sid": SID, "command": "git push"}
    assert P._desktop_answered(p) is None and reads
    reads.clear()
    assert P._desktop_answered(p) is None and reads == []     # 沒動過：一行都不讀
    append_jsonl(f, [tool_result("u9", "pushed")])
    assert P._desktop_answered(p) == "allow"
