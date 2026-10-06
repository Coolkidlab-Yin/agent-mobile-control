# -*- coding: utf-8 -*-
"""SQLite 落地：runs / run_events / pending / messages 四張表（2026-10 手機介面改版第一階段）。

只存「伺服器自己產生的狀態」——對話紀錄本身仍以 ~/.claude/projects 的 jsonl 為準，這裡不複製。
用途：伺服器重啟後 (1) 事件流能重播 (2) 待答的授權／提問卡不消失 (3) 手機重送同一句不會跑兩次。
標準庫 sqlite3，WAL，單一連線加鎖；每筆寫入都很小，直接在 event loop 上同步做。
"""
import json
import sqlite3
import threading
import time

from .config import BASE

DB_PATH = BASE / "state.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id   TEXT PRIMARY KEY,
  sid      TEXT,
  slug     TEXT,
  cwd      TEXT,
  status   TEXT NOT NULL,          -- starting / running / ended / failed / unknown
  transport TEXT NOT NULL,         -- claude / codex / api / peer
  started  REAL NOT NULL,
  ended    REAL,
  ok       INTEGER,                -- 1 / 0 / NULL(不明)
  error    TEXT,
  boot     TEXT NOT NULL           -- 哪一次伺服器啟動開的（重啟後用來找孤兒）
);
CREATE INDEX IF NOT EXISTS runs_sid ON runs(sid, started);
CREATE TABLE IF NOT EXISTS run_events (
  run_id  TEXT NOT NULL,
  seq     INTEGER NOT NULL,        -- 該 run 內從 0 起＝記憶體串列的索引，/events?start= 語意不變
  payload TEXT NOT NULL,
  PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS pending (
  id        TEXT PRIMARY KEY,
  kind      TEXT NOT NULL,         -- ask / perm
  run_id    TEXT,
  sid       TEXT,
  payload   TEXT NOT NULL,         -- 卡片本體（questions / tool / detail …）
  answer    TEXT,                  -- JSON；NULL = 還沒答
  by        TEXT,                  -- phone / desktop / timeout
  created   REAL NOT NULL,
  last_poll REAL,                  -- 等待程式最後一次來問的時間（心跳）
  boot      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS pending_open ON pending(answer, created);
CREATE TABLE IF NOT EXISTS messages (
  client_msg_id TEXT PRIMARY KEY,  -- 手機每次送出前產生；重試沿用同一個
  sid           TEXT,
  payload_hash  TEXT NOT NULL,
  run_id        TEXT,
  created       REAL NOT NULL
);
"""

_conn = None
_lock = threading.Lock()
BOOT_ID = str(int(time.time() * 1000))   # 這一次伺服器啟動的識別


def init(path=None):
    """開（或重開）資料庫；測試用 tmp_path 呼叫。"""
    global _conn, DB_PATH
    if path is not None:
        DB_PATH = path
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, isolation_level=None)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.executescript(_SCHEMA)
    return _conn


def _db():
    if _conn is None:
        init()
    return _conn


def _q(sql, args=()):
    with _lock:
        return _db().execute(sql, args)


# ---------- runs / run_events ----------

def run_open(run_id, sid, slug, cwd, transport):
    _q("INSERT OR REPLACE INTO runs(run_id,sid,slug,cwd,status,transport,started,ended,ok,error,boot)"
       " VALUES(?,?,?,?,?,?,?,NULL,NULL,NULL,?)",
       (run_id, sid, slug, cwd, "running", transport, time.time(), BOOT_ID))


def run_close(run_id, ok, error=None, status=None):
    """ok=True/False/None（None＝結果不明）。"""
    st = status or ("ended" if ok else "failed" if ok is False else "unknown")
    _q("UPDATE runs SET status=?, ended=?, ok=?, error=? WHERE run_id=?",
       (st, time.time(), None if ok is None else int(bool(ok)), error, run_id))


def event_append(run_id, seq, ev):
    _q("INSERT OR IGNORE INTO run_events(run_id,seq,payload) VALUES(?,?,?)",
       (run_id, seq, json.dumps(ev, ensure_ascii=False)))


def events_for(run_id):
    rows = _q("SELECT payload FROM run_events WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
    return [json.loads(r[0]) for r in rows]


def runs_open_from_other_boots():
    """上一次（或更早）啟動時還沒收尾的 run：重啟後要標成結果不明。"""
    rows = _q("SELECT run_id,sid,slug,cwd,transport,started FROM runs"
              " WHERE boot<>? AND status IN ('starting','running')", (BOOT_ID,)).fetchall()
    return [dict(zip(("run_id", "sid", "slug", "cwd", "transport", "started"), r, strict=True)) for r in rows]


def last_run_for(sid, within=3600):
    """這個對話最近一個 run 的落地狀態（給開房時判斷「上一輪是不是在重啟時中斷」）。"""
    r = _q("SELECT run_id,status,ok,error,started,ended FROM runs WHERE sid=? AND started>=?"
           " ORDER BY started DESC LIMIT 1", (sid, time.time() - within)).fetchone()
    if not r:
        return None
    d = dict(zip(("run_id", "status", "ok", "error", "started", "ended"), r, strict=True))
    d["n_events"] = _q("SELECT COUNT(*) FROM run_events WHERE run_id=?", (d["run_id"],)).fetchone()[0]
    return d


# ---------- pending（授權／提問卡） ----------

def pending_put(pid, kind, run_id, sid, payload):
    _q("INSERT OR REPLACE INTO pending(id,kind,run_id,sid,payload,answer,by,created,last_poll,boot)"
       " VALUES(?,?,?,?,?,NULL,NULL,?,NULL,?)",
       (pid, kind, run_id, sid, json.dumps(payload, ensure_ascii=False), time.time(), BOOT_ID))


def pending_answer(pid, answer, by):
    _q("UPDATE pending SET answer=?, by=? WHERE id=?", (json.dumps(answer, ensure_ascii=False), by, pid))


def pending_touch(pid):
    """等待程式來輪詢＝心跳。"""
    _q("UPDATE pending SET last_poll=? WHERE id=?", (time.time(), pid))


def pending_open(max_age):
    """還沒答、而且沒過期的卡（重啟後載回記憶體用）。"""
    rows = _q("SELECT id,kind,run_id,sid,payload,created,last_poll FROM pending"
              " WHERE answer IS NULL AND created>=? ORDER BY created", (time.time() - max_age,)).fetchall()
    out = []
    for r in rows:
        d = dict(zip(("id", "kind", "run_id", "sid", "payload", "created", "last_poll"), r, strict=True))
        d["payload"] = json.loads(d["payload"])
        out.append(d)
    return out


def pending_last_poll(pid):
    r = _q("SELECT last_poll FROM pending WHERE id=?", (pid,)).fetchone()
    return r[0] if r else None


# ---------- messages（送出去重） ----------

def message_claim(client_msg_id, sid, payload_hash):
    """回 ("new", None) / ("same", run_id) / ("conflict", None)。同鍵同內容＝重送，回既有結果；同鍵不同內容＝衝突。"""
    r = _q("SELECT payload_hash, run_id FROM messages WHERE client_msg_id=?", (client_msg_id,)).fetchone()
    if r is None:
        _q("INSERT INTO messages(client_msg_id,sid,payload_hash,run_id,created) VALUES(?,?,?,NULL,?)",
           (client_msg_id, sid, payload_hash, time.time()))
        return "new", None
    if r[0] != payload_hash:
        return "conflict", None
    return "same", r[1]


def message_bind(client_msg_id, run_id):
    _q("UPDATE messages SET run_id=? WHERE client_msg_id=?", (run_id, client_msg_id))


def message_release(client_msg_id):
    """送出在建 run 之前就失敗（4xx）：把鍵放掉，讓同一句改過再送不會被當衝突。"""
    _q("DELETE FROM messages WHERE client_msg_id=? AND run_id IS NULL", (client_msg_id,))


def prune(days=7):
    cut = time.time() - days * 86400
    _q("DELETE FROM run_events WHERE run_id IN (SELECT run_id FROM runs WHERE started<?)", (cut,))
    _q("DELETE FROM runs WHERE started<?", (cut,))
    _q("DELETE FROM pending WHERE created<?", (cut,))
    _q("DELETE FROM messages WHERE created<?", (cut,))
