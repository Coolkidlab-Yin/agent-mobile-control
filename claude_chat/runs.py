# -*- coding: utf-8 -*-
"""進行中工作的登記表：Run 物件、RUNS / BY_SESSION 兩張表、往事件流塞事件。其他模組只 import 這些物件，不重新賦值。

2026-10 起每個 run 與它的事件同步落地到 SQLite（store.py）：重啟後能認出「上一輪在重啟時還在跑」，
而且 /api/run/{id}/events?start=N 的 N 就是 run_events.seq，重啟前後語意一樣。"""
import asyncio
import logging
import time

from . import store

log = logging.getLogger("claude-chat")

RESTART_NOTE = "伺服器在這一輪進行中重啟，之後的過程沒有紀錄，結果不明"


class Run:
    def __init__(self, run_id, slug, sid, cwd):
        self.id = run_id
        self.slug = slug
        self.sid = sid
        self.cwd = cwd
        self.events = []
        self.cond = asyncio.Condition()
        self.done = False
        self.proc = None
        self.started = time.time()
        self.is_new = sid is None     # 這一輪是不是從手機開的新對話（做完要不要登錄進桌面 app）
        self.allow_all = False        # 「先問我」模式下使用者按了「這次工作全部允許」


RUNS = {}          # run_id -> Run
BY_SESSION = {}    # sid -> run_id


def register(run, transport):
    """登記一個剛建好的 run（記憶體兩張表＋資料庫）。transport：claude / codex / api / peer。"""
    RUNS[run.id] = run
    if run.sid:
        BY_SESSION[run.sid] = run.id
    try:
        store.run_open(run.id, run.sid, run.slug, run.cwd, transport)
    except Exception as e:   # 落地失敗不能擋工作本身，只是重啟後認不出它
        log.warning("store.run_open failed for %s: %s", run.id, e)


async def _emit(run, ev):
    async with run.cond:
        run.events.append(ev)
        if ev.get("kind") == "done":
            run.done = True
        run.cond.notify_all()
    try:
        store.event_append(run.id, len(run.events) - 1, ev)
        if ev.get("kind") == "done":
            store.run_close(run.id, ev.get("ok"), (ev.get("error") or None))
    except Exception as e:
        log.warning("store write failed for %s: %s", run.id, e)


def restore_from_store():
    """啟動時：上一次啟動留下、還沒收尾的 run 全部補一個「結果不明」的 done 事件並載回 RUNS（不進 BY_SESSION：
    它們的子行程已經跟著舊伺服器一起結束，不算進行中）。回補了幾個。"""
    n = 0
    for r in store.runs_open_from_other_boots():
        run = Run(r["run_id"], r["slug"], r["sid"], r["cwd"])
        run.started = r["started"]
        run.peer = r["transport"] == "peer"
        run.events = store.events_for(run.id)
        ev = {"kind": "done", "ok": None, "unknown": True, "sid": run.sid, "error": RESTART_NOTE}
        run.events.append(ev)
        run.done = True
        store.event_append(run.id, len(run.events) - 1, ev)
        store.run_close(run.id, None, RESTART_NOTE)
        RUNS[run.id] = run
        try:
            asyncio.get_running_loop().create_task(_gc_run(run.id))
        except RuntimeError:
            pass   # 沒有 event loop（測試裡直接呼叫）就不排回收
        n += 1
    return n


async def _gc_run(run_id, delay=900):
    await asyncio.sleep(delay)
    RUNS.pop(run_id, None)
