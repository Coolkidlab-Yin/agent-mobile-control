# -*- coding: utf-8 -*-
"""授權卡：先問我模式的逐項授權、桌面 session 的危險指令與選擇題也能在手機按。這裡是卡片的登記表與對帳邏輯；路由在 api。"""
import time

from .config import PROJECTS_DIR
from .jsonl import _loads

# ---------- 逐項授權（先問我模式 ＋ 桌面 session 的危險指令也能在手機按） ----------
PERMS = {}  # perm_id -> {"run_id"|None, "sid", "tool", "detail", "preview", "reason", "answer", "created"}
PERM_TTL = 60 * 60          # 沒人理的授權卡幾秒後不再列出（hook 那邊最多等 55 分鐘）


def _perm_card(perm_id, p):
    return {"kind": "perm", "perm_id": perm_id, "tool": p["tool"], "detail": p["detail"],
            "preview": p["preview"], "reason": p["reason"], "sid": p.get("sid"),
            "source": "desktop" if p.get("run_id") is None else "phone", "created": p["created"],
            "answer": p.get("answer"), "by": p.get("by") or "", "questions": p.get("questions")}


def _perm_key(inp):
    """拿來對「同一題」的鍵：Bash 用 command；AskUserQuestion 用第一題的問題文字。"""
    if not isinstance(inp, dict):
        return None
    if isinstance(inp.get("command"), str):
        return inp["command"]
    qs = inp.get("questions")
    if isinstance(qs, list) and qs and isinstance(qs[0], dict):
        return qs[0].get("question")
    return None


def _desktop_answered(p):
    """桌面那邊先按了（確認框關掉、指令跑了或被拒）→ 從 jsonl 找到這個 tool_use 有沒有 tool_result。"""
    sid, cmd = p.get("sid"), p.get("command")
    if not sid or not cmd:
        return None
    g = list(PROJECTS_DIR.glob(f"*/{sid}.jsonl"))
    if not g:
        return None
    try:
        size = g[0].stat().st_size
        # 固定只翻檔尾 256KB 在忙碌的對話會漏：10-01 一個 7.9MB 的 session，卡片建立後又長了 1MB，
        # 桌面早答過的 tool_result 落在窗口外，卡片就掛滿一小時、手機跳「好幾步前」的題。
        # 改成第一次對帳時記下當時檔尾往前 1MB 的位置，之後一律從那裡讀到檔尾，檔案再長都涵蓋得到。
        start = p.get("scan_from")
        if start is None:
            start = p["scan_from"] = max(0, size - 1024 * 1024)
        with open(g[0], "rb") as f:
            f.seek(start)
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    use_id = None
    for line in lines:
        rec = _loads(line)
        if not rec:
            continue
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if rec.get("type") == "assistant" and b.get("type") == "tool_use" and _perm_key(b.get("input")) == cmd:
                use_id = b.get("id")
            elif use_id and rec.get("type") == "user" and b.get("type") == "tool_result" and b.get("tool_use_id") == use_id:
                txt = str(b.get("content"))[:200].lower()
                return "deny" if ("denied" in txt or "拒絕" in txt or b.get("is_error")) else "allow"
    return None


def refresh_desktop_answer(p, now=None):
    """桌面 session 的卡：每 2 秒最多對一次 jsonl，看桌面是不是已經答了；答了就記成 desktop。
    手機來列卡時叫，hook 自己來輪詢時也要叫——不然桌面先答之後 hook 會一直輪詢到 9.5 分鐘逾時（10-06）。"""
    now = now or time.time()
    if p["answer"] is None and p.get("run_id") is None and now - p["created"] > 3 and now - p.get("checked", 0) > 2:
        p["checked"] = now
        d = _desktop_answered(p)
        if d:
            p["answer"], p["by"] = d, "desktop"


def pending_perms(sid=None, answered=False):
    """桌面 session 的授權卡：預設列還沒回答的（清單標記＋旁觀模式）；answered=True 列最近已回答的（讓手機把卡鎖起來）。"""
    now = time.time()
    out = []
    for pid, p in list(PERMS.items()):
        if now - p["created"] > PERM_TTL:
            PERMS.pop(pid, None)
            continue
        refresh_desktop_answer(p, now)
        if p.get("run_id") or (p["answer"] is not None) != answered:
            continue
        if sid and p.get("sid") != sid:
            continue
        out.append(_perm_card(pid, p))
    return out
