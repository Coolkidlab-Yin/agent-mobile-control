# -*- coding: utf-8 -*-
"""背景任務：從對話紀錄認出在背景跑的指令／子代理／工作流程，讀它們的輸出檔看進度。"""
import re
import tempfile
import time
from pathlib import Path

from .config import LIVE_DIR, PROJECTS_DIR
from .jsonl import _iso_epoch, _loads, _tool_detail
from .procs import _pid_alive
from .runs import BY_SESSION, RUNS

# ---------- 背景任務 ----------
# 對話裡在背景跑的指令（run_in_background，或超過時限被移到背景）、背景子代理、工作流程：
# 開工時 tool_result 寫下任務編號與輸出檔；結束時 CLI 塞一則 <task-notification>，
# 存法有三種（queue-operation enqueue / attachment queued_command / user 字串）。
# 只認這三種紀錄型態的通知——同樣的字串被引用在別的工具輸出裡不算數。
# 一律 .match（從工具結果開頭比對）：CLI 自己的開工訊息就在開頭；
# 同一句話若只是被某個指令印出來（例如 grep 舊紀錄），在結果中間，不算數
_BG_ID = {
    "bash": re.compile(r"Command (?:running in background with ID: |did not complete within .*? "
                       r"and was moved to the background \(ID: )(\w+)"),
    "agent": re.compile(r"Async agent launched successfully\.[\s\S]*?agentId: (\w+)"),
    "workflow": re.compile(r"Workflow launched in background\. Task ID: (\w+)"),
}
_BG_OUT = re.compile(r"(?:Output is being written to|output_file): (\S.*?\.output)")
_BG_DIR = re.compile(r"Transcript dir: (.+?)(?= \||\n|$)")
_BG_SUMMARY = re.compile(r"Summary: (.+?)(?= \||\n|$)")
_BG_NOTE_ID = re.compile(r"<task-id>(\w+)</task-id>")
_BG_NOTE_STATUS = re.compile(r"<status>(\w+)</status>")
_BG_NOTE_SUM = re.compile(r"<summary>([\s\S]*?)</summary>")
_BG_MARKERS = (b"run_in_background", b"background", b"agentId", b"task-notification",
               b'"name":"Bash"', b'"name":"Agent"', b'"name":"Task"', b'"name":"Workflow"')
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# 輸出檔路徑是從對話紀錄裡讀來的文字：只准讀 CLI 自己的暫存任務目錄，工作流程目錄只准在 projects 底下，
# 不然一段被竄改的工具輸出就能叫伺服器把任意 .output 檔的內容吐給手機
_BG_TEMP_ROOT = (Path(tempfile.gettempdir()) / "claude").resolve()
_BG_CACHE = {}   # jsonl 路徑 -> {"off", "uses", "tasks"}


def _bg_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _bg_inside(p, root):
    try:
        rp = Path(p).resolve()
        return rp if rp.is_relative_to(root) else None
    except (OSError, ValueError):
        return None


def _bg_record(rec, st):
    typ, ts = rec.get("type"), _iso_epoch(rec.get("timestamp"))
    content = (rec.get("message") or {}).get("content")
    note = None
    if typ == "queue-operation" and rec.get("operation") == "enqueue":
        note = rec.get("content")
    elif typ == "attachment" and (rec.get("attachment") or {}).get("type") == "queued_command":
        note = rec["attachment"].get("prompt")
    elif typ == "user" and isinstance(content, str):
        note = content
    if isinstance(note, str) and note.lstrip().startswith("<task-notification>"):
        m = _BG_NOTE_ID.search(note)
        task = st["tasks"].get(m.group(1)) if m else None
        if task and task["status"] == "running":
            s = _BG_NOTE_STATUS.search(note)
            task["status"] = s.group(1) if s else "completed"
            task["ended"] = ts
            sm = _BG_NOTE_SUM.search(note)
            if sm:
                task["summary"] = sm.group(1).strip()[:300]
        return
    if not isinstance(content, list):
        return
    for b in content:
        if not isinstance(b, dict):
            continue
        if typ == "assistant" and b.get("type") == "tool_use":
            name, inp = b.get("name"), b.get("input") or {}
            if name in ("Bash", "Agent", "Task", "Workflow"):
                label = (inp.get("description") or inp.get("subagent_type") or _tool_detail(name, inp) or name)
                st["uses"][b.get("id")] = (name, str(label).replace("\n", " ")[:120])
        elif typ == "user" and b.get("type") == "tool_result":
            use = st["uses"].pop(b.get("tool_use_id"), None)   # 用過就丟，記憶體不會一直長
            if not use:
                continue
            txt = _bg_text(b.get("content"))
            kind = {"Bash": "bash", "Agent": "agent", "Task": "agent", "Workflow": "workflow"}[use[0]]
            m = _BG_ID[kind].match(txt.lstrip())
            if not m:
                continue   # 一般的前景呼叫
            label = use[1]
            if kind == "workflow":
                sm = _BG_SUMMARY.search(txt)
                label = sm.group(1).strip()[:120] if sm else label
            om, dm = _BG_OUT.search(txt), _BG_DIR.search(txt)
            st["tasks"][m.group(1)] = {
                "id": m.group(1), "kind": kind, "label": label, "started": ts,
                "status": "running", "ended": None, "summary": "",
                "out": om.group(1).strip() if om else "", "dir": dm.group(1).strip() if dm else ""}


def _bg_scan(f):
    """增量讀：記住讀到哪，只解析新寫進來的完整行；檔案變小（被換掉）就從頭來。"""
    key = str(f)
    size = f.stat().st_size
    st = _BG_CACHE.get(key)
    if st is None or size < st["off"]:
        st = _BG_CACHE[key] = {"off": 0, "uses": {}, "tasks": {}}
    if size > st["off"]:
        with open(f, "rb") as fh:
            fh.seek(st["off"])
            data = fh.read(size - st["off"])
        end = data.rfind(b"\n")
        if end >= 0:
            st["off"] += end + 1
            for line in data[:end].split(b"\n"):
                if any(mk in line for mk in _BG_MARKERS):   # 先粗篩，大部分行不用解 JSON
                    rec = _loads(line.decode("utf-8", "replace"))
                    if rec:
                        _bg_record(rec, st)
    return st


def _bg_progress(t):
    """回 (最後動靜的時間, 最近輸出的幾行)。"""
    if t["kind"] == "workflow":
        d = _bg_inside(t["dir"], PROJECTS_DIR.resolve()) if t["dir"] else None
        files = list(d.rglob("*.jsonl")) if d and d.is_dir() else []
        if not files:
            return None, []
        return max(p.stat().st_mtime for p in files), [f"已派出 {len(files)} 個子代理"]
    p = _bg_inside(t["out"], _BG_TEMP_ROOT) if t["out"] else None
    if not p or not p.is_file():
        return None, []
    st = p.stat()
    with open(p, "rb") as fh:
        fh.seek(max(0, st.st_size - (96 * 1024 if t["kind"] == "agent" else 4096)))
        raw = fh.read().decode("utf-8", "replace")
    if t["kind"] == "bash":
        lines = [ln.rstrip() for ln in _ANSI.sub("", raw).replace("\r", "\n").split("\n") if ln.strip()]
        return st.st_mtime, [ln[:200] for ln in lines[-8:]]
    # 子代理：輸出檔就是它自己的對話紀錄，抓最近幾個動作
    acts = []
    for line in raw.split("\n")[1:]:   # 第一行可能被切半
        rec = _loads(line)
        if not rec or rec.get("type") != "assistant":
            continue
        for b in (rec.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                acts.append("▸ " + b.get("name", "") + "　" + _tool_detail(b.get("name"), b.get("input"))[:120])
            elif isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip():
                acts.append("💬 " + b["text"].strip().replace("\n", " ")[:160])
    return st.st_mtime, acts[-5:]


def _session_alive_since(sid):
    """這個對話目前活著的程序裡最早的啟動時間（epoch 秒）；一個都沒活著回 None。"""
    starts = []
    rid = BY_SESSION.get(sid)
    if rid and rid in RUNS and not RUNS[rid].done:
        starts.append(RUNS[rid].started)
    try:
        for p in LIVE_DIR.glob("*.json"):
            d = _loads(p.read_text("utf-8")) or {}
            if d.get("sessionId") == sid and d.get("pid") and _pid_alive(d["pid"]):
                starts.append((d.get("startedAt") or 0) / 1000)
    except OSError:
        pass
    return min(starts) if starts else None


def list_bg_tasks(f, sid):
    """這個對話的背景任務：進行中的全列，結束的只列 6 小時內，最多 20 筆（前 6 筆附最近輸出）。"""
    st = _bg_scan(f)
    since = _session_alive_since(sid)
    now = time.time()

    def status_of(t):
        # 背景任務是開它的那個程序的子行程；程序結束它就跟著沒了，而且不會留下完成通知。
        # 同一個對話編號會被續用很多次（手機每句都起新程序），所以「對話還活著」不夠：
        # 任務必須是在目前活著的程序啟動之後才開的，才可能還在跑
        if t["status"] == "running" and (since is None or t["started"] < since - 5):
            return "unknown"
        return t["status"]

    tasks = [(status_of(t), t) for t in st["tasks"].values()]
    tasks = [(s, t) for s, t in tasks if s == "running" or now - (t["ended"] or t["started"]) < 6 * 3600]
    tasks.sort(key=lambda x: (x[0] != "running", -x[1]["started"]))
    out = []
    for status, t in tasks[:20]:
        last, tail = _bg_progress(t) if len(out) < 6 else (None, [])
        out.append({"id": t["id"], "kind": t["kind"], "label": t["label"], "status": status,
                    "started": t["started"], "ended": t["ended"], "summary": t["summary"],
                    "last": last, "tail": tail})
    return {"tasks": out, "now": now}
