"""造假對話紀錄的小工具。格式照 Claude Code 寫的 jsonl：一行一筆、緊湊 JSON（沒有空白）——
伺服器有幾處先用位元組關鍵字粗篩（例如 "name":"Bash"），寬鬆格式會讓那些行被跳過，測出來的不是真的。"""
import json
from datetime import datetime, timezone

TS = "2026-10-04T01:00:00.000Z"


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def dump(rec):
    return json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(dump(r))
    return path


def append_jsonl(path, records):
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(dump(r))
    return path


def user(text, ts=None, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, "timestamp": ts or TS, **extra}


def assistant(blocks, ts=None, **extra):
    if isinstance(blocks, str):
        blocks = [{"type": "text", "text": blocks}]
    return {"type": "assistant", "message": {"role": "assistant", "content": blocks}, "timestamp": ts or TS, **extra}


def tool_use(name, inp, uid):
    return {"type": "tool_use", "name": name, "input": inp, "id": uid}


def tool_result(uid, content, is_error=False, ts=None):
    block = {"type": "tool_result", "tool_use_id": uid, "content": content, "is_error": is_error}
    return {"type": "user", "message": {"role": "user", "content": [block]}, "timestamp": ts or TS}
