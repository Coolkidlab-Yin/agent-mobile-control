"""工作台（手機首頁）：每間房一張卡，分進 待處理／執行中／已完成 三段並掛徽章。
分段只用有來源的訊號（docs/mobile-ia-plan-2026-10.md §4）：
  待答卡＝PERMS/ASKS 裡等待程式還活著的；失敗／結果不明＝SQLite runs；
  執行中＝手機 run 活著、觀測 mod 回報 turn.start、或 jsonl 幾秒內有寫入；已完成＝最近一個 run 正常結束。
沒有訊號就不掛徽章，絕不編「進度」。classify()/build() 是純函式，資料來源用參數注入，好測。"""
import time

FAILED_WINDOW = 24 * 3600   # 失敗／結果不明的 run 留在待處理多久（還沒有「已知悉」機制，先用時間窗）
DONE_WINDOW = 48 * 3600     # 多久內正常結束的 run 才掛「已完成」
NOTE_MAX = 140


def _questions(card):
    if card.get("questions"):
        return card["questions"]
    for key in ("input", "tool_input"):
        q = (card.get(key) or {}).get("questions") if isinstance(card.get(key), dict) else None
        if q:
            return q
    return []


def first_question(card):
    qs = _questions(card)
    if qs and isinstance(qs[0], dict):
        return (qs[0].get("question") or "")[:NOTE_MAX]
    return ""


def perm_summary(card):
    for key in ("summary", "command", "path", "detail"):
        v = card.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:NOTE_MAX]
    inp = card.get("input") or card.get("tool_input") or {}
    if isinstance(inp, dict):
        for key in ("command", "file_path", "path", "url"):
            v = inp.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()[:NOTE_MAX]
    return card.get("tool") or ""


def classify(room, pending, last_run, obs, now=None):
    """回 (section, badge, badge_kind, note)。
    順序：等你回答 → 等你授權 → 失敗／結果不明 → 執行中 → 已完成 → 沒徽章。"""
    now = now or time.time()
    open_cards = [c for c in pending if not c.get("waiter_gone")]
    asks = [c for c in open_cards if c.get("tool") == "AskUserQuestion"]
    perms = [c for c in open_cards if c.get("tool") != "AskUserQuestion"]
    if asks:
        return "todo", "等你回答", "ask", first_question(asks[0])
    if perms:
        return "todo", "等你授權", "perm", perm_summary(perms[0])
    lr = last_run or {}
    recent = bool(lr) and (lr.get("ended") or lr.get("started") or 0) >= now - FAILED_WINDOW
    if recent and lr.get("ok") == 0:
        return "todo", "出了問題", "failed", (lr.get("error") or "")[:NOTE_MAX]
    if recent and lr.get("status") == "unknown":
        return "todo", "結果不明", "unknown", "伺服器在這一輪進行中重啟，之後沒有紀錄"
    if room.get("running"):
        return "running", "執行中", "running", "手機起的工作進行中"
    if obs and obs.get("executing"):
        return "running", "執行中", "running", "桌面對話正在執行"
    if room.get("busy"):
        return "running", "執行中", "running", "桌面剛剛還在寫入紀錄"
    if bool(lr) and lr.get("ok") == 1 and (lr.get("ended") or 0) >= now - DONE_WINDOW:
        return "done", "已完成", "done", ""
    return "done", "", "none", ""


def build(rooms, pending, last_run_for, observed, now=None):
    """rooms：scan_rooms() 的列；pending：pending_cards()；last_run_for(sid, within)、observed(sid) 注入。
    回 {"cards": [room 欄位 + section/badge/badge_kind/note/last_run/executing/n_pending], "counts": {...}}。"""
    now = now or time.time()
    by_sid = {}
    for c in pending:
        if c.get("sid"):
            by_sid.setdefault(c["sid"], []).append(c)
    cards = []
    counts = {"todo": 0, "running": 0, "done": 0}
    for r in rooms:
        sids = r.get("gen_sids") or [r["sid"]]
        pend = [c for s in sids for c in by_sid.get(s, [])]
        runs = [x for x in (last_run_for(s, DONE_WINDOW) for s in sids) if x]
        lr = max(runs, key=lambda x: x.get("started") or 0) if runs else None
        obs = next((o for o in (observed(s) for s in sids) if o), None)
        section, badge, kind, note = classify(r, pend, lr, obs, now)
        card = dict(r, section=section, badge=badge, badge_kind=kind, note=note,
                    last_run=({k: lr.get(k) for k in ("status", "ok", "ended", "error")} if lr else None),
                    executing=bool(obs and obs.get("executing")),
                    n_pending=sum(1 for c in pend if not c.get("waiter_gone")))
        counts[section] += 1
        cards.append(card)
    return {"cards": cards, "counts": counts}
