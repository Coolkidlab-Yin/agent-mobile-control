# -*- coding: utf-8 -*-
"""進行中工作的登記表：Run 物件、RUNS / BY_SESSION 兩張表、往事件流塞事件。其他模組只 import 這些物件，不重新賦值。"""
import asyncio
import time


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


async def _emit(run, ev):
    async with run.cond:
        run.events.append(ev)
        if ev.get("kind") == "done":
            run.done = True
        run.cond.notify_all()


async def _gc_run(run_id, delay=900):
    await asyncio.sleep(delay)
    RUNS.pop(run_id, None)
