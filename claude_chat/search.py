# -*- coding: utf-8 -*-
"""全文搜尋對話內容。"""
from pathlib import Path

from .config import API_CHATS, PROJECTS_DIR
from .jsonl import _is_meta_user, _loads, _text_of
from .rooms import scan_rooms

SEARCH_TAIL = 1_500_000   # 每個對話只翻最後 1.5MB
SEARCH_MAX_ROOMS = 40


def _search_file(path, q, engine):
    """在一個對話檔裡找 q（不分大小寫），回最多 3 段摘錄。"""
    hits = []
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > SEARCH_TAIL:
                f.seek(size - SEARCH_TAIL)
                f.readline()   # 丟掉切半的那行
            data = f.read()
    except OSError:
        return hits
    ql = q.lower()
    for line in data.decode("utf-8", "replace").splitlines():
        if ql not in line.lower():
            continue
        rec = _loads(line)
        if not rec:
            continue
        if engine == "codex":
            if rec.get("type") != "event_msg":
                continue
            p = rec.get("payload") or {}
            if p.get("type") not in ("user_message", "agent_message"):
                continue
            text = p.get("message") or ""
            role = "user" if p["type"] == "user_message" else "assistant"
        else:
            role = rec.get("type") if rec.get("type") in ("user", "assistant") else rec.get("role")
            if role not in ("user", "assistant"):
                continue
            msg = rec.get("message") or rec
            text = _text_of(msg.get("content"))
            if role == "user" and _is_meta_user(rec, text):
                continue
        i = text.lower().find(ql)
        if i < 0:
            continue
        start = max(0, i - 40)
        snippet = text[start:start + 120].replace("\n", " ")
        hits.append({"role": role, "snippet": ("…" if start else "") + snippet,
                     "ts": rec.get("timestamp")})
        if len(hits) >= 3:
            break
    return hits


def search_rooms(q, show_all=False):
    """全文搜尋聊天內容（桌面 app 的「搜尋 session 內容」）。q 至少兩個字，由路由先擋。"""
    out = []
    for room in scan_rooms(show_all=show_all):
        eng = room.get("engine", "claude")
        if eng == "codex":
            path = Path(room["path"])
        elif eng != "claude":
            path = API_CHATS / eng / (room["sid"] + ".jsonl")
        else:
            path = PROJECTS_DIR / room["slug"] / (room["sid"] + ".jsonl")
        hits = _search_file(path, q, eng)
        if hits:
            out.append({"slug": room["slug"], "sid": room["sid"], "title": room["title"],
                        "project_name": room["project_name"], "engine": eng,
                        "ts": room["ts"], "hits": hits})
        if len(out) >= SEARCH_MAX_ROOMS:
            break
    return {"q": q, "rooms": out}
