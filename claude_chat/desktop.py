# -*- coding: utf-8 -*-
"""桌面 app 的 session 登錄檔（標題／封存／cwd）與 claude:// 深層連結。"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .config import HOME
from .jsonl import _clean_title

# ---------- 桌面 app 的 session 登錄 ----------
# 桌面 app 把每個 session 存成一個 json（Windows 在 %APPDATA%\Claude\claude-code-sessions\<帳號>\<組織>\local_<id>.json），
# 裡面的 cliSessionId 就是 ~/.claude/projects 那個 jsonl 的檔名 —— 兩邊靠這個對齊，不用再猜時間。
# 手機開的對話要進桌面 app：桌面 app 有註冊 claude:// 協定，claude://resume?session=<cli sid> 會把
# 磁碟上的 jsonl 匯進登錄（同一份紀錄，不複製），之後兩邊都寫同一個檔。直接寫登錄 json 桌面 app 不會即時吃到，實測過。
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_desktop_reg_cache = {"at": 0.0, "data": {}}
_desktop_reg_files = {}   # 登錄檔路徑 -> (mtime_ns, size, cli sid, entry)
DESKTOP_REG_TTL = 5.0


def _desktop_reg_dirs():
    cands = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        cands.append(Path(appdata) / "Claude" / "claude-code-sessions")
    cands.append(HOME / "Library" / "Application Support" / "Claude" / "claude-code-sessions")
    return [p for p in cands if p.is_dir()]


def desktop_registry():
    """cli session id -> {local_id, title, archived, cwd, last(epoch 秒)}；沒有桌面 app 就是空 dict。
    400 多個登錄檔每 5 秒全部重讀要 0.36 秒（手機列清單八成時間都在這）——改成按 (mtime, size) 只重讀有變的檔。"""
    now = time.time()
    if now - _desktop_reg_cache["at"] < DESKTOP_REG_TTL:
        return _desktop_reg_cache["data"]
    out = {}
    seen = set()
    for root in _desktop_reg_dirs():
        for f in root.glob("*/*/local_*.json"):
            try:
                st = f.stat()
            except OSError:
                continue
            key = str(f)
            seen.add(key)
            hit = _desktop_reg_files.get(key)
            if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
                if hit[2]:
                    out[hit[2]] = hit[3]
                continue
            try:
                d = json.loads(f.read_text("utf-8"))
            except Exception:
                d = None
            if not isinstance(d, dict):
                _desktop_reg_files[key] = (st.st_mtime_ns, st.st_size, None, None)
                continue
            local_id = d.get("sessionId") or f.stem
            cli = d.get("cliSessionId") or local_id.replace("local_", "", 1)
            last = d.get("lastActivityAt") or d.get("createdAt") or 0
            entry = {
                "local_id": local_id,
                "title": _clean_title(d.get("title") or ""),
                "archived": bool(d.get("isArchived")),
                "cwd": d.get("cwd") or "",
                "last": (last / 1000.0) if isinstance(last, (int, float)) else 0,
            }
            _desktop_reg_files[key] = (st.st_mtime_ns, st.st_size, cli, entry)
            out[cli] = entry
    for k in [k for k in _desktop_reg_files if k not in seen]:
        del _desktop_reg_files[k]
    _desktop_reg_cache.update(at=now, data=out)
    return out


def desktop_app_available():
    """桌面 app 有沒有裝（看 claude:// 協定有沒有註冊）。"""
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\claude\shell\open\command"):
                return True
        except OSError:
            return False
    if sys.platform == "darwin":
        return (Path("/Applications/Claude.app").exists()
                or (HOME / "Applications" / "Claude.app").exists())
    return False


def desktop_open(sid):
    """把一個 CLI session 匯進桌面 app 並切過去；已經在登錄裡的就只是切過去。
    回 'imported' / 'opened' / 'unavailable'。"""
    if not sid or not _UUID_RE.match(sid):
        raise ValueError("session id 格式不對")
    if not desktop_app_available():
        return "unavailable"
    reg = desktop_registry().get(sid)
    if reg:
        url = f"claude://code/continue?session={reg['local_id']}"
    else:
        url = f"claude://resume?session={sid}"
    if sys.platform == "win32":
        os.startfile(url)  # noqa: S606 - 交給系統的協定處理器（桌面 app）
    else:
        subprocess.Popen(["open", url])
    _desktop_reg_cache["at"] = 0.0
    return "opened" if reg else "imported"
