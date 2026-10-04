# -*- coding: utf-8 -*-
"""聊天室清單：掃對話紀錄、跟桌面登錄對齊、compact 世代合併、封存／改名／手機開的對話名單；找房間檔。"""
import json
import os
import re
import time
from pathlib import Path

from fastapi import HTTPException

from .config import (
    API_CHATS,
    API_SLUG,
    APP_SESSIONS,
    BUSY_WINDOW,
    CODEX_SESSIONS,
    ENGINES,
    LIVE_DIR,
    MATCH_TOLERANCE,
    MAX_ROOMS,
    PROJECTS_DIR,
    SLUG_ENGINE,
    SNAP_FILE,
    TITLES_FILE,
    WEB_ARCHIVE,
    log,
)
from .desktop import desktop_registry
from .jsonl import _clean_title, _head_info, _iso_epoch, _loads, _norm_cwd, _project_name, _slug_encode, _tail_info
from .perm import pending_perms
from .procs import _pid_alive
from .runs import BY_SESSION

# ---------- 房間列表（含快取） ----------

_room_cache = {}  # str(path) -> (mtime_ns, size, room_dict)


def live_session_ids():
    out = set()
    try:
        for p in LIVE_DIR.glob("*.json"):
            try:
                d = json.loads(p.read_text("utf-8"))
            except Exception:
                continue
            sid, pid = d.get("sessionId"), d.get("pid")
            if sid and pid and _pid_alive(pid):
                out.add(sid)
    except OSError:
        pass
    return out


_snap_cache = {"mtime": None, "data": None}


_overlay_cache = {"mtime": None, "data": {}}
_app_sids_cache = {"mtime": None, "data": set()}


def load_app_sids():
    """這個 App 自己開過的對話 id。

    從這裡送出的訊息是用 `claude -p` 跑的，Claude Code 記下來的 entrypoint 會是
    sdk-cli — 跟排程機器人同一類。少了這份名單，使用者在手機上開的新對話
    會被 sdk-cli 那條過濾規則一起藏掉，開完就從列表上消失。
    """
    try:
        m = APP_SESSIONS.stat().st_mtime
    except OSError:
        return set()
    if _app_sids_cache["mtime"] != m:
        try:
            data = json.loads(APP_SESSIONS.read_text("utf-8"))
            _app_sids_cache["data"] = set(data) if isinstance(data, list) else set()
            _app_sids_cache["mtime"] = m
        except Exception:
            return set()
    return _app_sids_cache["data"]


def remember_app_sid(sid):
    if not sid:
        return
    sids = set(load_app_sids())
    if sid in sids:
        return
    sids.add(sid)
    tmp = APP_SESSIONS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sorted(sids), indent=1), "utf-8")
    os.replace(tmp, APP_SESSIONS)
    _app_sids_cache["mtime"] = None


_titles_cache = {"mtime": None, "data": {}}


def load_titles():
    try:
        m = TITLES_FILE.stat().st_mtime
    except OSError:
        return {}
    if _titles_cache["mtime"] != m:
        try:
            d = json.loads(TITLES_FILE.read_text("utf-8"))
            _titles_cache["data"] = d if isinstance(d, dict) else {}
            _titles_cache["mtime"] = m
        except Exception:
            return {}
    return _titles_cache["data"]


def save_title(sid, title):
    d = dict(load_titles())
    if title:
        d[sid] = title
    else:
        d.pop(sid, None)
    tmp = TITLES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, TITLES_FILE)
    _titles_cache["mtime"] = None


def load_overlay():
    """網頁端的封存標記：{sid: "archived"|"active"}，疊在桌面快照之上。"""
    try:
        m = WEB_ARCHIVE.stat().st_mtime
    except OSError:
        return {}
    if _overlay_cache["mtime"] != m:
        try:
            _overlay_cache["data"] = json.loads(WEB_ARCHIVE.read_text("utf-8"))
            _overlay_cache["mtime"] = m
        except Exception:
            return {}
    return _overlay_cache["data"] or {}


def save_overlay(d):
    # 先寫暫存再原子替換：中途斷電也不會留下半個檔案讓下次讀取整份失效
    tmp = WEB_ARCHIVE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, WEB_ARCHIVE)
    _overlay_cache["mtime"] = None


def load_snapshot():
    """桌面 app 的 session 登錄（標題 + 封存狀態）。

    有桌面 app 的機器直接讀它的登錄 json（即時、用 cliSessionId 精準對齊）；
    沒有才退回手動匯出的 desktop-sessions.json 快照。
    """
    reg = desktop_registry()
    if reg:
        return {"generated_at": time.time(), "live": True,
                "sessions": {sid: {"title": r["title"], "archived": r["archived"],
                                   "cwd": r["cwd"], "last": r["last"]}
                             for sid, r in reg.items()}}
    empty = {"generated_at": 0, "sessions": {}}
    try:
        m = SNAP_FILE.stat().st_mtime
    except OSError:
        return empty
    if _snap_cache["mtime"] != m:
        try:
            _snap_cache["data"] = json.loads(SNAP_FILE.read_text("utf-8"))
            _snap_cache["mtime"] = m
        except Exception:
            log.warning("snapshot unreadable")
            return empty
    return _snap_cache["data"] or empty


def _file_infos():
    infos = []
    try:
        for proj in PROJECTS_DIR.iterdir():
            if not proj.is_dir():
                continue
            for f in proj.glob("*.jsonl"):
                if f.name.startswith("agent-"):
                    continue
                try:
                    st = f.stat()
                except OSError:
                    continue
                if st.st_size < 200:
                    continue
                key = str(f)
                cached = _room_cache.get(key)
                if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
                    info = dict(cached[2])
                else:
                    head = _head_info(f)
                    tail = _tail_info(f)
                    cwd = head["cwd"] or ""
                    info = {
                        "slug": proj.name,
                        "sid": f.stem,
                        "project": cwd,
                        "project_name": (cwd.rstrip("\\/").split("\\")[-1] if cwd else proj.name),
                        "title": head["title"] or "（沒有標題）",
                        "entry": head["entry"] or "cli",
                        "custom_title": head["custom_title"],
                        "compacted": head["compacted"],
                        "preview": tail["preview"],
                        "ts": tail["ts"],
                        "last_epoch": _iso_epoch(tail["ts"]) or st.st_mtime,
                        "mtime": st.st_mtime,
                    }
                    _room_cache[key] = (st.st_mtime_ns, st.st_size, dict(info))
                infos.append(info)
    except OSError:
        pass
    return infos


_codex_cache = {}


def _codex_info(f):
    """從 codex rollout jsonl 抽 cwd/標題/預覽（只讀 event_msg 層，跳過系統提示）。"""
    info = {"cwd": "", "title": "", "preview": "", "ts": None, "sid": ""}
    try:
        lines = f.read_bytes().decode("utf-8", "replace").splitlines()
    except OSError:
        return info
    last_user = last_ai = ""
    for line in lines:
        rec = _loads(line)
        if not rec:
            continue
        pl = rec.get("payload") or {}
        t = rec.get("type")
        if t == "session_meta":
            info["cwd"] = pl.get("cwd") or ""
            info["sid"] = pl.get("session_id") or pl.get("id") or ""
        elif t == "event_msg":
            pt = pl.get("type")
            if pt == "user_message":
                m = (pl.get("message") or "").strip()
                if m and not m.startswith("<"):
                    if not info["title"]:
                        info["title"] = _clean_title(m)
                    last_user = m
                    last_ai = ""
            elif pt == "agent_message":
                last_ai = (pl.get("message") or "").strip()
        if rec.get("timestamp"):
            info["ts"] = rec["timestamp"]
    info["preview"] = _clean_title(last_ai) if last_ai else ("你：" + _clean_title(last_user) if last_user else "")
    return info


def codex_rooms():
    rooms = []
    if not CODEX_SESSIONS.exists():
        return rooms
    for f in CODEX_SESSIONS.rglob("rollout-*.jsonl"):
        try:
            st = f.stat()
        except OSError:
            continue
        if st.st_size < 400:
            continue
        key = str(f)
        cached = _codex_cache.get(key)
        if not (cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size):
            info = _codex_info(f)
            if not info["sid"] or not info["title"]:
                info = None
            cached = (st.st_mtime_ns, st.st_size, info)
            _codex_cache[key] = cached
        info = cached[2]
        if not info:
            continue
        cwd = info["cwd"]
        rooms.append({
            "engine": "codex", "slug": "codex", "sid": info["sid"], "path": str(f),
            "project": cwd, "project_name": _project_name(cwd, "codex"),
            "title": info["title"], "preview": info["preview"], "ts": info["ts"],
            "last_epoch": _iso_epoch(info["ts"]) or st.st_mtime, "mtime": st.st_mtime,
            "archived": False, "live": False, "entry": "codex",
        })
    return rooms


def api_rooms():
    rooms = []
    if not API_CHATS.exists():
        return rooms
    for eng_dir in API_CHATS.iterdir():
        eng = eng_dir.name
        if eng not in ENGINES or not eng_dir.is_dir():
            continue
        for f in eng_dir.glob("*.jsonl"):
            try:
                st = f.stat()
            except OSError:
                continue
            title = preview = ""
            ts = None
            model = ENGINES[eng]["model"]
            try:
                for line in f.read_bytes().decode("utf-8", "replace").splitlines():
                    r = _loads(line)
                    if not r:
                        continue
                    ts = r.get("ts") or ts
                    model = r.get("model") or model
                    txt = (r.get("content") or "").strip()
                    if not txt:
                        continue
                    if r.get("role") == "user":
                        if not title:
                            title = _clean_title(txt)
                        preview = "你：" + _clean_title(txt)
                    else:
                        preview = _clean_title(txt)
            except OSError:
                continue
            if not title:
                continue
            rooms.append({
                "engine": eng, "slug": API_SLUG[eng], "sid": f.stem, "path": str(f),
                "project": "", "project_name": ENGINES[eng]["label"], "model": model,
                "title": title, "preview": preview, "ts": ts,
                "last_epoch": _iso_epoch(ts) or st.st_mtime, "mtime": st.st_mtime,
                "archived": False, "live": False, "entry": "api",
            })
    return rooms


def _merge_compact_generations(infos, live=frozenset()):
    """把同一場對話的多個 compact 世代併成一列。

    每次 /compact（或自動壓縮）Claude Code 都會換一個新的 session id、
    另開一個 jsonl，把壓縮後的完整歷史複製進去。不合併的話，一場聊了整天、
    compact 過五次的對話會在列表上長成五間，而且每一間的標題都是使用者
    當時講的第一句話 —— 看起來就像「每講一句話就開一間」。

    合併鍵用 (custom-title, cwd)：custom-title 是桌面 app 顯示的名字，同一場
    對話的所有世代都一樣。沒有 custom-title 的檔（早期版本、CLI 開的）維持獨立，
    不靠猜的欄位去併，寧可少併也不要把兩場不同的對話併成一場。
    """
    groups = {}
    out = []
    for fi in infos:
        ct = fi.get("custom_title")
        if not ct:
            out.append(fi)
            continue
        groups.setdefault((ct, _norm_cwd(fi.get("project"))), []).append(fi)

    for gen_list in groups.values():
        if len(gen_list) == 1:
            out.append(gen_list[0])
            continue
        # 最新世代代表這場對話：點進去接續的必須是它，接到舊世代等於回到 compact 前。
        # 桌面正開著其中一個世代（有活著的行程）時一律以它為準：手機訊息才會直送進那個行程、
        # 旁觀進度也才尾讀到正在寫的那個檔。
        gen_list.sort(key=lambda x: x["last_epoch"], reverse=True)
        live_gen = next((g for g in gen_list if g["sid"] in live), None)
        newest = dict(live_gen or gen_list[0])
        newest["generations"] = len(gen_list)
        newest["gen_sids"] = [g["sid"] for g in gen_list]
        out.append(newest)
    return out


def scan_rooms(show_all=False):
    """檔案 × 桌面登錄 對齊：
    - 檔名直接命中登錄 id → 綁定
    - 否則用 (專案路徑, 最後活動時間±180s) 最近鄰配對
    可見 = 桌面未封存 + 一般 CLI 對話 + 快照之後的新桌面對話；
    隱藏 = 已封存、排程機器人(sdk-cli)、舊桌面殘檔。
    """
    live = live_session_ids()
    infos = _merge_compact_generations(_file_infos(), live)
    snap = load_snapshot()
    sessions = snap.get("sessions", {})
    gen_at = snap.get("generated_at", 0)

    # slug 反查 cwd（有些檔頭讀不到 cwd）
    slug_map = {}
    for reg in sessions.values():
        if reg.get("cwd"):
            slug_map.setdefault(_slug_encode(reg["cwd"]), reg["cwd"])
    for fi in infos:
        if fi["project"]:
            slug_map.setdefault(_slug_encode(fi["project"]), fi["project"])
    for fi in infos:
        if not fi["project"] and fi["slug"] in slug_map:
            fi["project"] = slug_map[fi["slug"]]
        fi["project_name"] = _project_name(fi["project"], fi["slug"])

    assigned = {}          # file sid -> registry uuid
    used_reg = set()
    for fi in infos:
        if fi["sid"] in sessions:
            assigned[fi["sid"]] = fi["sid"]
            used_reg.add(fi["sid"])

    cands = []
    for fi in infos:
        if fi["sid"] in assigned or not fi["last_epoch"]:
            continue
        ncwd = _norm_cwd(fi["project"])
        for rid, reg in sessions.items():
            if rid in used_reg or _norm_cwd(reg.get("cwd")) != ncwd:
                continue
            d = abs(fi["last_epoch"] - reg.get("last", 0))
            if d <= MATCH_TOLERANCE:
                cands.append((d, fi["sid"], rid))
    cands.sort()
    for _d, fsid, rid in cands:
        if fsid in assigned or rid in used_reg:
            continue
        assigned[fsid] = rid
        used_reg.add(rid)

    overlay = load_overlay()
    app_sids = load_app_sids()
    rooms = []
    for fi in infos:
        room = dict(fi)
        # 合併過的列要用整組世代來判斷，不能只看代表那一代的 sid
        sids = fi.get("gen_sids") or [fi["sid"]]
        rid = next((assigned[x] for x in sids if x in assigned), None)
        reg = sessions.get(rid) if rid else None
        archived = bool(reg and reg.get("archived"))
        if reg and reg.get("title") and not fi.get("custom_title"):
            # custom-title 是桌面 app 當下顯示的名字，比快照裡的舊標題新
            room["title"] = reg["title"]
        # 來源規則（先不管封存）
        if any(x in app_sids for x in sids):
            # 這個 App 自己開的對話。它的 entrypoint 也是 sdk-cli，
            # 所以要排在那條過濾規則前面，否則使用者一開新對話就看不到了
            base = True
        elif reg:
            base = True
        elif fi["entry"] == "sdk-cli":
            base = False
        elif fi["entry"] == "claude-desktop":
            base = fi["last_epoch"] > gen_at - 3600
        else:
            base = True
        # 網頁端封存標記覆蓋桌面快照（任一世代被標記就算數）
        ov = next((overlay[x] for x in sids if x in overlay), None)
        if ov == "archived":
            archived = True
        elif ov == "active":
            # 明確解封＝使用者說「我要看到這個」，連上面的來源規則一起蓋掉。
            # 這是被自動規則藏錯的對話唯一的救回方式。
            archived = False
            base = True
        room["archived"] = archived
        visible = base and not archived
        if not visible and not show_all:
            continue
        room["hidden"] = not visible
        room["live"] = any(x in live for x in sids)
        room["engine"] = "claude"
        room["desktop"] = bool(reg)                      # 桌面 app 登錄裡有它
        room["app"] = any(x in app_sids for x in sids)   # 是從手機開的
        room["busy"] = (time.time() - fi["mtime"]) < BUSY_WINDOW   # 桌面那邊正在寫＝工作中
        rooms.append(room)

    for extra in codex_rooms() + api_rooms():
        ov = overlay.get(extra["sid"])
        extra["archived"] = ov == "archived"
        if extra["archived"] and not show_all:
            continue
        rooms.append(extra)

    titles = load_titles()
    _pp = pending_perms()
    perm_sids = {p["sid"] for p in _pp if p["tool"] != "AskUserQuestion"}
    ask_sids = {p["sid"] for p in _pp if p["tool"] == "AskUserQuestion"}
    for room in rooms:
        room["running"] = room["sid"] in BY_SESSION
        room["perm"] = any(x in perm_sids for x in (room.get("gen_sids") or [room["sid"]]))
        room["ask"] = any(x in ask_sids for x in (room.get("gen_sids") or [room["sid"]]))
        t = titles.get(room["sid"])
        if t:
            room["title"] = t
    rooms.sort(key=lambda r: r["last_epoch"], reverse=True)
    return rooms[: (900 if show_all else MAX_ROOMS + 120)]


def _safe_name(v, pattern):
    """單一路徑片段的檢查：形狀對，而且不是 . 或 ..（會跳出目錄）。"""
    return bool(re.fullmatch(pattern, v or "")) and v not in (".", "..")


def find_room_file(slug, sid):
    if not _safe_name(slug, r"[A-Za-z0-9._-]+") or not _safe_name(sid, r"[A-Za-z0-9-]+"):
        raise HTTPException(400, "壞掉的參數")
    eng = SLUG_ENGINE.get(slug)
    if eng == "codex":
        for r in codex_rooms():
            if r["sid"] == sid:
                return Path(r["path"])
        raise HTTPException(404, "找不到這個 Codex 對話")
    if eng:
        f = API_CHATS / eng / (sid + ".jsonl")
        if not f.exists():
            raise HTTPException(404, "找不到這個聊天室")
        return f
    f = PROJECTS_DIR / slug / (sid + ".jsonl")
    if not f.exists():
        raise HTTPException(404, "找不到這個聊天室")
    return f
