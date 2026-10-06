# -*- coding: utf-8 -*-
"""對話紀錄（~/.claude/projects/*/*.jsonl）與 stream-json 事件的解析、正規化成前端可播的 items。純函式。"""
import json
import re

from .config import HEAD_BYTES, TAIL_BYTES

# ---------- jsonl 解析 ----------

META_PREFIXES = (
    "<local-command", "<command-name", "Caveat:", "<system-reminder",
    "<task-notification", "[Request interrupted",
)


def _loads(line):
    try:
        return json.loads(line)
    except Exception:
        return None


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
        return "\n".join(parts)
    return ""


def _is_meta_user(rec, text):
    if rec.get("isMeta") or rec.get("isCompactSummary"):
        return True
    t = text.lstrip()
    if not t:
        return True
    return t.startswith(META_PREFIXES) or t.startswith("This session is being continued")


def _fmt_tokens(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "?"
    return f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def compact_item(rec, ts=None):
    """Claude Code 2.1.283 起 /compact 只在紀錄裡留一筆 system/compact_boundary（不再寫
    isCompactSummary 的 user 條目），手機要靠它才知道對話被壓縮過。認不得就回 None。"""
    if rec.get("type") != "system" or rec.get("subtype") != "compact_boundary":
        return None
    meta = rec.get("compactMetadata") or {}
    how = "手動" if meta.get("trigger") == "manual" else "自動"
    text = f"{how}壓縮：前文 {_fmt_tokens(meta.get('preTokens'))} tokens 收成 {_fmt_tokens(meta.get('postTokens'))} tokens"
    return {"role": "user", "kind": "info", "text": text, "label": "對話已壓縮", "ts": ts}


def _tool_detail(name, inp):
    if not isinstance(inp, dict):
        return ""
    for k in ("command", "file_path", "pattern", "description", "prompt",
              "url", "query", "skill", "title"):
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().replace("\n", " ")[:160]
    return ""


def _clean_title(t):
    t = re.sub(r"\s+", " ", t or "").strip()
    return t[:64] if t else ""


def items_from_message(role, message, ts=None, tool_status=None):
    """jsonl 記錄與 stream-json 事件共用的正規化。"""
    items = []
    content = (message or {}).get("content")
    if isinstance(content, str):
        if content.strip():
            items.append({"role": role, "kind": "text", "text": content, "ts": ts})
        return items
    if not isinstance(content, list):
        return items
    for b in content:
        if not isinstance(b, dict):
            continue
        bt = b.get("type")
        if bt == "text":
            if (b.get("text") or "").strip():
                items.append({"role": role, "kind": "text", "text": b["text"], "ts": ts})
        elif bt == "tool_use":
            name = b.get("name", "?")
            if name.startswith("mcp__"):
                name = name.split("__")[-1]
            item = {"role": "assistant", "kind": "tool", "tool": name,
                    "detail": _tool_detail(b.get("name"), b.get("input")),
                    "tool_use_id": b.get("id"), "ok": None, "ts": ts}
            if tool_status is not None and b.get("id") in tool_status:
                item["ok"] = tool_status[b.get("id")]
            items.append(item)
            # 桌面 app 的「傳檔案給使用者」卡片手機看不到（工具輸入是 files 陣列，
            # 內文又常只寫檔名），把路徑補成一則文字讓前端變成內嵌播放器/圖片
            if name == "SendUserFile" and isinstance(b.get("input"), dict):
                files = [f for f in (b["input"].get("files") or []) if isinstance(f, str)]
                if files:
                    cap = b["input"].get("caption")
                    lines = ([cap.strip()] if isinstance(cap, str) and cap.strip() else []) + files
                    items.append({"role": "assistant", "kind": "text", "text": "\n".join(lines), "ts": ts})
    return items


def _iter_tail_lines(path, max_bytes):
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            f.readline()  # 丟掉可能被切半的行
        data = f.read()
    return data.decode("utf-8", "replace").splitlines()


def _head_info(path):
    """讀檔頭：cwd、entrypoint、標題、是否為 compact 後的世代。

    標題優先用 `custom-title`（就是桌面 app 顯示的那個名字，寫在檔案第一行）。
    退回「第一句提問」只在沒有 custom-title 時 —— 對 compact 後產生的檔，
    第一句提問是使用者當下講的那句話，拿來當標題會變成滿滿的自言自語。
    """
    info = {"cwd": None, "title": "", "entry": None,
            "custom_title": "", "compacted": False}
    try:
        with open(path, "rb") as f:
            data = f.read(HEAD_BYTES)
        lines = data.decode("utf-8", "replace").splitlines()
    except OSError:
        return info
    first_q = first_user = ""
    for line in lines:
        rec = _loads(line)
        if not rec:
            continue
        if info["cwd"] is None and isinstance(rec.get("cwd"), str):
            info["cwd"] = rec["cwd"]
        if info["entry"] is None and isinstance(rec.get("entrypoint"), str):
            info["entry"] = rec["entrypoint"]
        if not info["custom_title"] and rec.get("type") == "custom-title":
            ct = rec.get("customTitle")
            if isinstance(ct, str) and ct.strip():
                info["custom_title"] = _clean_title(ct)
        if not info["compacted"] and rec.get("compactMetadata"):
            info["compacted"] = True
        if not first_q and rec.get("type") == "queue-operation":
            c = rec.get("content")
            if isinstance(c, str) and c.strip():
                first_q = _clean_title(c)
        if not first_user and rec.get("type") == "user":
            t = _text_of((rec.get("message") or {}).get("content"))
            if t and not _is_meta_user(rec, t):
                first_user = _clean_title(t)
        if info["cwd"] and info["entry"] and info["custom_title"] and info["compacted"]:
            break
    info["title"] = info["custom_title"] or first_q or first_user
    return info


def _tail_info(path):
    """讀檔尾：最後訊息預覽、最後時間。"""
    info = {"preview": "", "ts": None}
    try:
        lines = _iter_tail_lines(path, TAIL_BYTES)
    except OSError:
        return info
    for line in reversed(lines):
        rec = _loads(line)
        if not rec:
            continue
        if info["ts"] is None and isinstance(rec.get("timestamp"), str):
            info["ts"] = rec["timestamp"]
        if rec.get("isSidechain"):
            continue
        rt = rec.get("type")
        if rt == "assistant":
            t = _text_of((rec.get("message") or {}).get("content"))
            if t.strip():
                info["preview"] = _clean_title(t)
                break
        elif rt == "user":
            t = _text_of((rec.get("message") or {}).get("content"))
            if t.strip() and not _is_meta_user(rec, t):
                info["preview"] = "你：" + _clean_title(t)
                break
    return info


def _iso_epoch(iso):
    if not iso:
        return 0.0
    try:
        from datetime import datetime
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _norm_cwd(p):
    return (p or "").replace("/", "\\").rstrip("\\").casefold()


def _slug_encode(p):
    return re.sub(r"[^A-Za-z0-9]", "-", p or "")


def _project_name(cwd, slug):
    """worktree 顯示上層專案名；沒 cwd 時退回 slug 尾段。"""
    if not cwd:
        return slug.split("-")[-1] if slug else "?"
    parts = cwd.rstrip("\\/").replace("/", "\\").split("\\")
    if ".claude" in parts:
        i = parts.index(".claude")
        if 0 < i and i + 1 < len(parts) and parts[i + 1] == "worktrees":
            return parts[i - 1]
    return parts[-1]


def _ctx_window(model, tokens):
    """上下文視窗估算：Fable 走 1M，其他 200k；超過 190k 一律視為 1M。"""
    if "fable" in (model or "").lower() or tokens > 190_000:
        return 1_000_000
    return 200_000
