# -*- coding: utf-8 -*-
"""歷史訊息：Claude / Codex / API 三種紀錄轉成聊天泡泡；尾讀（旁觀桌面正在跑的對話）。"""
import time

from .config import BUSY_WINDOW, MAX_HISTORY_BYTES
from .jsonl import _ctx_window, _is_meta_user, _loads, _text_of, compact_item, items_from_message


def codex_history(path, limit=120):
    """Codex rollout → 聊天泡泡（走 event_msg 層）。"""
    items = []
    ctx = None
    try:
        lines = path.read_bytes().decode("utf-8", "replace").splitlines()
    except OSError:
        return {"items": [], "oldest": 0, "more": False, "context": None}
    win = 0
    for idx, line in enumerate(lines):
        rec = _loads(line)
        if not rec or rec.get("type") != "event_msg":
            continue
        pl = rec.get("payload") or {}
        pt = pl.get("type")
        ts = rec.get("timestamp")
        if pt == "user_message":
            m = (pl.get("message") or "").strip()
            if m and not m.startswith("<"):
                items.append({"i": idx, "role": "user", "kind": "text", "text": m, "ts": ts})
        elif pt == "agent_message":
            m = (pl.get("message") or "").strip()
            if m:
                items.append({"i": idx, "role": "assistant", "kind": "text", "text": m, "ts": ts})
        elif pt == "agent_reasoning" or pt == "exec_command_begin":
            cmd = pl.get("command") or pl.get("text") or ""
            if pt == "exec_command_begin" and cmd:
                items.append({"i": idx, "role": "assistant", "kind": "tool", "tool": "Shell",
                              "detail": (" ".join(cmd) if isinstance(cmd, list) else str(cmd))[:160],
                              "ok": True, "ts": ts})
        elif pt == "task_started":
            win = pl.get("model_context_window") or win
        elif pt == "token_count":
            info = pl.get("info") or {}
            tot = info.get("total_token_usage") or {}
            tok = (tot.get("input_tokens") or 0) + (tot.get("cached_input_tokens") or 0)
            if tok and win:
                ctx = {"tokens": tok, "window": win, "pct": round(tok * 100 / win)}
    more = len(items) > limit
    return {"items": items[-limit:], "oldest": 0, "more": more, "context": ctx}


def api_history(path, limit=200):
    items = []
    try:
        lines = path.read_bytes().decode("utf-8", "replace").splitlines()
    except OSError:
        lines = []
    for idx, line in enumerate(lines):
        r = _loads(line)
        if not r or not (r.get("content") or "").strip():
            continue
        items.append({"i": idx, "role": r.get("role", "assistant"), "kind": "text",
                      "text": r["content"], "ts": r.get("ts")})
    return {"items": items[-limit:], "oldest": 0, "more": len(items) > limit, "context": None}


# ---------- 歷史訊息 ----------

def load_history(path, before=None, limit=120):
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > MAX_HISTORY_BYTES:
            f.seek(size - MAX_HISTORY_BYTES)
            f.readline()
        lines = f.read().decode("utf-8", "replace").splitlines()

    tool_status = {}
    items = []
    context = None
    reached_start = True
    for idx in range(len(lines) - 1, -1, -1):
        if before is not None and idx >= before:
            continue
        rec = _loads(lines[idx])
        if not rec or rec.get("isSidechain"):
            continue
        rt = rec.get("type")
        ts = rec.get("timestamp")
        if rt == "user":
            msg = rec.get("message") or {}
            content = msg.get("content")
            # 先收集 tool_result 狀態
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        tool_status[b.get("tool_use_id")] = not b.get("is_error", False)
            text = _text_of(content)
            if rec.get("isCompactSummary"):
                items.append({"i": idx, "role": "user", "kind": "info",
                              "text": text, "label": "前情摘要", "ts": ts})
                continue
            if text.strip() and not _is_meta_user(rec, text):
                items.append({"i": idx, "role": "user", "kind": "text", "text": text, "ts": ts})
        elif rt == "assistant":
            msg = rec.get("message") or {}
            if context is None and isinstance(msg.get("usage"), dict):
                u = msg["usage"]
                tok = ((u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                       + (u.get("cache_creation_input_tokens") or 0))
                if tok > 0:
                    win = _ctx_window(msg.get("model"), tok)
                    context = {"tokens": tok, "window": win,
                               "pct": round(tok * 100 / win)}
            got = items_from_message("assistant", msg, ts, tool_status)
            for it in reversed(got):
                it["i"] = idx
                items.append(it)
        elif rt == "system":
            it = compact_item(rec, ts)
            if it:
                it["i"] = idx
                items.append(it)
        if len(items) >= limit:
            reached_start = False
            break
    items.reverse()
    return {"items": items, "oldest": (items[0]["i"] if items else 0),
            "more": not reached_start, "context": context, "size": size}


def _room_busy(path):
    try:
        return time.time() - path.stat().st_mtime < BUSY_WINDOW
    except OSError:
        return False


def tail_items(path, offset):
    """從 offset 讀新寫入的紀錄（只吃完整的行），轉成前端可播的 items。回 (items, new_offset)。
    桌面正在跑的 session 也能在手機上即時看進度（工具呼叫、背景工作通知、回覆）。"""
    try:
        size = path.stat().st_size
    except OSError:
        return [], offset
    if size < offset:
        offset = 0          # 檔案被換掉/截短，從頭算
    if size == offset:
        return [], offset
    with open(path, "rb") as f:
        f.seek(offset)
        chunk = f.read(size - offset)
    nl = chunk.rfind(b"\n")
    if nl < 0:
        return [], offset
    items = []
    tool_status = {}
    for line in chunk[:nl].decode("utf-8", "replace").splitlines():
        rec = _loads(line)
        if not rec or rec.get("isSidechain"):
            continue
        rt = rec.get("type")
        ts = rec.get("timestamp")
        msg = rec.get("message") or {}
        if rt == "user":
            content = msg.get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        ok = not b.get("is_error", False)
                        tool_status[b.get("tool_use_id")] = ok
                        items.append({"kind": "tool_ok", "tool_use_id": b.get("tool_use_id"), "ok": ok})
            text = _text_of(content)
            if rec.get("isCompactSummary"):
                items.append({"role": "user", "kind": "info", "text": text, "label": "前情摘要", "ts": ts})
            elif text.strip():
                if text.lstrip().startswith("<task-notification>"):
                    items.append({"role": "user", "kind": "info", "text": text, "label": "背景工作回報", "ts": ts})
                elif not _is_meta_user(rec, text):
                    items.append({"role": "user", "kind": "text", "text": text, "ts": ts})
        elif rt == "system":
            it = compact_item(rec, ts)
            if it:
                items.append(it)
        elif rt == "assistant":
            for it in items_from_message("assistant", msg, ts, tool_status):
                items.append(it)
            u = msg.get("usage")
            if isinstance(u, dict):
                tok = ((u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                       + (u.get("cache_creation_input_tokens") or 0))
                if tok > 0:
                    win = _ctx_window(msg.get("model"), tok)
                    items.append({"kind": "ctx", "tokens": tok, "window": win, "pct": round(tok * 100 / win)})
    return items, offset + nl + 1
