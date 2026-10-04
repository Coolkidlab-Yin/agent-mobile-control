# -*- coding: utf-8 -*-
"""方案額度（Anthropic OAuth usage API）與本機 token 統計。"""
import json
import time

from .config import HOME, PROJECTS_DIR
from .jsonl import _loads

_limits_cache = {"ts": 0.0, "data": None}
LIMIT_LABELS = {"session": "5 小時限額", "weekly_all": "每週 · 全模型"}


def plan_limits():
    """方案額度（跟桌面 app 同一個來源：Anthropic OAuth usage API），快取 60 秒。"""
    import urllib.error
    import urllib.request
    if _limits_cache["data"] and time.time() - _limits_cache["ts"] < 60:
        return _limits_cache["data"]
    try:
        cred = json.loads((HOME / ".claude" / ".credentials.json").read_text("utf-8"))
        tok = cred.get("claudeAiOauth", {}).get("accessToken", "")
        if not tok:
            return {"ok": False, "error": "本機沒有登入憑證"}
        req = urllib.request.Request(
            "https://api.anthropic.com/api/oauth/usage",
            headers={"Authorization": "Bearer " + tok,
                     "anthropic-beta": "oauth-2025-04-20",
                     "Content-Type": "application/json",
                     "User-Agent": "claude-chat/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        msg = "憑證過期了，在電腦上用一次 claude 就會自動刷新" if e.code == 401 else f"HTTP {e.code}"
        return {"ok": False, "error": msg}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    rows = []
    for lim in raw.get("limits") or []:
        label = LIMIT_LABELS.get(lim.get("kind"))
        if not label:
            scope = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
            label = "每週 · " + scope if scope else (lim.get("kind") or "?")
        rows.append({"label": label, "percent": lim.get("percent", 0),
                     "resets_at": lim.get("resets_at"),
                     "severity": lim.get("severity", "normal")})
    credits = None
    ex = raw.get("extra_usage") or {}
    if ex.get("is_enabled"):
        dp = 10 ** ex.get("decimal_places", 2)
        credits = {"used": (ex.get("used_credits") or 0) / dp,
                   "limit": (ex.get("monthly_limit") or 0) / dp,
                   "currency": ex.get("currency", "USD")}
    out = {"ok": True, "limits": rows, "credits": credits}
    _limits_cache.update(ts=time.time(), data=out)
    return out


MODEL_LABELS = {
    "claude-fable-5": "Fable 5",
    "claude-opus-5": "Opus 5",
    "claude-sonnet-5": "Sonnet 5",
    "claude-haiku-4-5-20251001": "Haiku 4.5",
}
_usage_cache = {}  # path -> (mtime_ns, size, {(day, model): [msgs, in, out, cr, cw]})


def _usage_of_file(f):
    from datetime import datetime
    agg = {}
    seen = set()
    try:
        lines = f.read_bytes().decode("utf-8", "replace").splitlines()
    except OSError:
        return agg
    for line in lines:
        rec = _loads(line)
        if not rec or rec.get("type") != "assistant":
            continue
        msg = rec.get("message") or {}
        u = msg.get("usage")
        if not isinstance(u, dict):
            continue
        mid = rec.get("requestId") or msg.get("id")
        if mid and mid in seen:
            continue
        if mid:
            seen.add(mid)
        ts = rec.get("timestamp")
        try:
            day = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%m-%d")
        except Exception:
            continue
        model = msg.get("model") or "?"
        model = MODEL_LABELS.get(model, model)
        k = (day, model)
        a = agg.setdefault(k, [0, 0, 0, 0, 0])
        a[0] += 1
        a[1] += u.get("input_tokens", 0) or 0
        a[2] += u.get("output_tokens", 0) or 0
        a[3] += u.get("cache_read_input_tokens", 0) or 0
        a[4] += u.get("cache_creation_input_tokens", 0) or 0
    return agg


def token_usage(days=7):
    """從本機對話紀錄統計真實 token 用量（含排程與 subagent）。"""
    from datetime import datetime
    cutoff = time.time() - (min(days, 30) + 1) * 86400
    merged = {}
    try:
        for proj in PROJECTS_DIR.iterdir():
            if not proj.is_dir():
                continue
            for f in proj.glob("*.jsonl"):
                try:
                    st = f.stat()
                except OSError:
                    continue
                if st.st_mtime < cutoff or st.st_size < 200:
                    continue
                key = str(f)
                cached = _usage_cache.get(key)
                if not (cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size):
                    cached = (st.st_mtime_ns, st.st_size, _usage_of_file(f))
                    _usage_cache[key] = cached
                for k, v in cached[2].items():
                    a = merged.setdefault(k, [0, 0, 0, 0, 0])
                    for i in range(5):
                        a[i] += v[i]
    except OSError:
        pass

    today = datetime.now().strftime("%m-%d")
    day_series = {}
    model_totals = {}
    today_tot = [0, 0, 0, 0, 0]
    week_tot = [0, 0, 0, 0, 0]
    for (day, model), v in merged.items():
        d = day_series.setdefault(day, [0, 0, 0, 0, 0])
        m = model_totals.setdefault(model, [0, 0, 0, 0, 0])
        for i in range(5):
            d[i] += v[i]
            m[i] += v[i]
            week_tot[i] += v[i]
            if day == today:
                today_tot[i] += v[i]

    def pack(a):
        return {"msgs": a[0], "in": a[1], "out": a[2], "cache_read": a[3], "cache_write": a[4]}

    return {
        "today": pack(today_tot),
        "window": pack(week_tot),
        "days": sorted(({"date": d, **pack(v)} for d, v in day_series.items()),
                       key=lambda x: x["date"], reverse=True),
        "models": {m: pack(v) for m, v in
                   sorted(model_totals.items(), key=lambda kv: -kv[1][2])},
    }
