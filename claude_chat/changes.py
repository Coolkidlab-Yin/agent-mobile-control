# -*- coding: utf-8 -*-
"""成果與變更（手機介面改版第二階段，docs/mobile-ia-plan-2026-10.md §3、§7）。

四層分開、各有來源，不混成一句「改好了」：
  1. 本對話修改紀錄   ＝ jsonl 裡 Edit / Write / MultiEdit / NotebookEdit 的 tool_result（自帶 structuredPatch）
  2. 相關檔案的目前差異 ＝ git diff HEAD -- <第 1 層的檔>（含未追蹤的另列）
  3. 其他工作區變更   ＝ git status 裡不在第 1 層的檔（只給數量與檔名，不暗示跟本對話無關）
  4. 本對話觸發的提交 ＝ Bash 輸出裡 `[branch sha] subject` 經 git cat-file 驗證；另列「對話期間的提交」（git log --since）
測試結果三欄分開：指令結束狀態（is_error / Exit code N）／測試判定（從輸出解析出的數字）／證據（transcript）。
新鮮度：測試之後還有修改 → stale；伺服器在測試落地時記下被改檔案的內容指紋，之後對得上 → current；都沒有 → unverified。
掃描結果按 (size, mtime) 快取；git 指令都是唯讀、5 秒逾時、失敗就當沒有 repo。"""
import hashlib
import re
import subprocess
import threading
import time
from pathlib import Path

from .jsonl import _iso_epoch, _loads

EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
# 只認「在指令段開頭位置」的測試執行器：行首或 ; && || | ( 之後，前面可以有 VAR=值、cmd //c／time 這類包裝、路徑前綴。
# 出現在字串或路徑中間的不算（grep "@pytest"、sed ... tests/test_ui.py、pip install pytest-xdist），
# 不然 grep 一下就被記成「跑過測試」，卡片上最近一次測試會變成那個 grep。
_RUNNERS = (r"(?:(?:python[\w.]*|py|\$\w+) -m (?:pytest|unittest)|pytest|npm (?:run )?test|npx (?:vitest|jest|playwright test)|"
            r"vitest|jest|go test|cargo test|check\.cmd|node --test|bun test|mix test|rspec|phpunit)")
TEST_RE = re.compile(
    r"(?:^|[;&|(]+\s*)"                                              # 指令段的開頭
    r"(?:[A-Za-z_]\w*=\S*\s+)*"                                      # VAR=值 ...
    r"(?:(?:cmd (?://|/)c|time|exec|uv run|poetry run|pdm run)\s+)?"  # 包裝
    r"(?:[\w.:~$-]*[/\\](?:[\w.-]+[/\\])*)?"                         # 路徑前綴：C:/x/、./node_modules/.bin/
    + _RUNNERS + r"(?![\w-])", re.M)
COMMIT_RE = re.compile(r"^\[([^\]\s]+)(?: \([^)]*\))? ([0-9a-f]{7,40})\] (.*)$", re.M)
EXIT_RE = re.compile(r"^(?:Error: )?Exit code (\d+)", re.M)
GIT_TIMEOUT = 5
OUTPUT_TAIL = 600          # 測試輸出只留尾巴這麼多字（總結行都在尾巴）
MAX_HUNKS_PER_FILE = 200   # ponytail: 一個檔超過這麼多差異塊就只留最後的；真要全看進對話
_CACHE = {}
_CACHE_LOCK = threading.Lock()


# ---------- 第 1 層＋測試＋提交：掃對話紀錄 ----------

def _result_text(block):
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(x.get("text", "") for x in c if isinstance(x, dict))
    return ""


def parse_test_summary(text):
    """從測試輸出撈數字。認得 pytest、jest/vitest、check.cmd；撈不到回 None（不猜）。"""
    tail = text[-4000:]
    m = re.findall(r"(\d+) (passed|failed|error(?:s)?|skipped|deselected|xfailed|xpassed)\b", tail)
    counts = {}
    for n, k in m:
        k = "errors" if k.startswith("error") else k
        counts[k] = counts.get(k, 0) + int(n)   # pytest 一行只出現一次；jest 的 "Tests:" 行也是各一次
    if not counts:
        m = re.search(r"Tests:\s+(.+)", tail)
        if m:
            for n, k in re.findall(r"(\d+) (passed|failed|skipped|todo)", m.group(1)):
                counts[k] = int(n)
    if counts:
        return counts
    if "All checks passed" in tail or "CHECK OK" in tail:
        return {"checks": "ok"}
    if "CHECK FAILED" in tail:
        return {"checks": "failed"}
    return None


def verdict_text(ok, exit_code, counts):
    if counts and "checks" in counts:
        return "檢查通過" if counts["checks"] == "ok" else "檢查失敗"
    if counts:
        parts = []
        for k, label in (("passed", "過"), ("failed", "失敗"), ("errors", "錯誤"), ("skipped", "略過")):
            if counts.get(k):
                parts.append("%d %s" % (counts[k], label))
        return "、".join(parts) if parts else "有總結但看不懂"
    if ok:
        return "指令成功，輸出裡沒有測試總結"
    return "指令失敗（exit %s）" % (exit_code if exit_code is not None else "?")


def scan_transcript(path):
    """單次掃完整個 jsonl。回 {"edits": {abs: {...}}, "tests": [...], "commits": [...], "first_ts", "last_edit_ts"}。"""
    out = {"edits": {}, "tests": [], "commits": [], "first_ts": None, "last_edit_ts": None}
    uses = {}   # tool_use_id -> (name, input, ts)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return out
    for line in raw.decode("utf-8", "replace").splitlines():
        if '"tool_use"' not in line and '"tool_result"' not in line:   # 粗篩：純文字的行不用解析
            continue
        rec = _loads(line)
        if not rec:
            continue
        ts = _iso_epoch(rec.get("timestamp")) if isinstance(rec.get("timestamp"), str) else None
        if out["first_ts"] is None and ts:
            out["first_ts"] = ts
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        rtype = rec.get("type")
        for b in content:
            if not isinstance(b, dict):
                continue
            if rtype == "assistant" and b.get("type") == "tool_use":
                name = b.get("name")
                if name in EDIT_TOOLS or name == "Bash":
                    uses[b.get("id")] = (name, b.get("input") or {}, ts)
            elif rtype == "user" and b.get("type") == "tool_result":
                use = uses.pop(b.get("tool_use_id"), None)
                if not use:
                    continue
                name, inp, uts = use
                tur = rec.get("toolUseResult")
                if name in EDIT_TOOLS:
                    if b.get("is_error"):
                        continue
                    fp = ((tur.get("filePath") if isinstance(tur, dict) else None)
                          or inp.get("file_path") or inp.get("notebook_path"))
                    if not isinstance(fp, str) or not fp:
                        continue
                    # 修改的時間用「結果回來」那一刻（檔案真的寫進去了），跟測試用的是同一種時間，
                    # 新鮮度才能比「測試之後有沒有再改」；tool_use 那一刻只是 Claude 發出指令。
                    ets = ts or uts
                    e = out["edits"].setdefault(fp, {"n": 0, "first_ts": ets, "last_ts": ets, "kinds": [],
                                                     "hunks": [], "created": False})
                    e["n"] += 1
                    e["last_ts"] = ets or e["last_ts"]
                    if name not in e["kinds"]:
                        e["kinds"].append(name)
                    if isinstance(tur, dict):
                        if tur.get("type") == "create":
                            e["created"] = True
                        sp = tur.get("structuredPatch")
                        if isinstance(sp, list) and sp:
                            e["hunks"].append({"ts": ets, "hunks": sp})
                            e["hunks"] = e["hunks"][-MAX_HUNKS_PER_FILE:]
                    out["last_edit_ts"] = max(out["last_edit_ts"] or 0, ets or 0) or None
                else:   # Bash
                    cmd = inp.get("command") or ""
                    text = _result_text(b)
                    if TEST_RE.search(cmd):
                        is_err = bool(b.get("is_error"))
                        m = EXIT_RE.search(text) if is_err else None
                        exit_code = int(m.group(1)) if m else (0 if not is_err else None)
                        counts = parse_test_summary(text)
                        out["tests"].append({
                            "tool_use_id": b.get("tool_use_id"), "ts": ts, "command": cmd[:300],
                            "ok": not is_err, "exit_code": exit_code, "counts": counts,
                            "verdict": verdict_text(not is_err, exit_code, counts),
                            "tail": text[-OUTPUT_TAIL:],
                        })
                    if "git commit" in cmd and not b.get("is_error"):
                        for branch, sha, subject in COMMIT_RE.findall(text):
                            out["commits"].append({"sha": sha, "branch": branch, "subject": subject[:200], "ts": ts})
    return out


def scan_cached(path):
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return scan_transcript(path)
    key = str(path)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] == (st.st_size, st.st_mtime_ns):
            return hit[1]
    res = scan_transcript(path)
    with _CACHE_LOCK:
        _CACHE[key] = ((st.st_size, st.st_mtime_ns), res)
    return res


def summary(path, fingerprints=None):
    """給工作台卡片的一行：改了幾個檔、最後一次測試怎樣。沒有就 None，不編。
    fingerprints：{tool_use_id: 指紋}（store.test_fingerprints），有才算得出 current。"""
    s = scan_cached(path)
    if not s["edits"] and not s["tests"]:
        return None
    last = pick_test(s["tests"])
    return {
        "n_files": len(s["edits"]), "n_edits": sum(e["n"] for e in s["edits"].values()),
        "last_edit_ts": s["last_edit_ts"],
        "test": ({"ok": last["ok"], "verdict": last["verdict"], "ts": last["ts"],
                  "freshness": freshness(last, s, (fingerprints or {}).get(last["tool_use_id"]))} if last else None),
    }


def pick_test(tests):
    """卡片要顯示哪一次測試：從最後往前找第一個「有總結」或「失敗」的。
    「指令成功但輸出裡沒有總結」（被 head/tail 切掉、-x 提早停）是雜訊，跳過；但失敗永遠不會被更早的綠燈蓋掉。"""
    for t in reversed(tests):
        if t["counts"] or not t["ok"]:
            return t
    return tests[-1] if tests else None


def freshness(test, scan, fingerprint=None):
    """current / stale / unverified。測試之後還有修改一律 stale；有落地指紋且對得上才 current。"""
    t = test.get("ts") or 0
    if scan["last_edit_ts"] and scan["last_edit_ts"] > t + 1:
        return "stale"
    if fingerprint:
        return "current" if fingerprint == file_fingerprint(scan["edits"].keys()) else "stale"
    return "unverified"


def file_fingerprint(paths):
    out = {}
    for p in sorted(paths):
        try:
            out[p] = hashlib.sha1(Path(p).read_bytes()).hexdigest()[:16]
        except OSError:
            out[p] = None
    return out


# ---------- 第 2～4 層：git（唯讀） ----------

def _git(cwd, *args):
    try:
        r = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=cwd, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def repo_root(cwd):
    if not cwd or not Path(cwd).is_dir():
        return None
    out = _git(cwd, "rev-parse", "--show-toplevel")
    return out.strip() if out else None


def _rel(root, p):
    try:
        return Path(p).resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _norm(root):
    """repo 根的比對鍵：路徑大小寫與斜線方向在 Windows 上都不穩定。"""
    try:
        return Path(root).resolve().as_posix().casefold()
    except OSError:
        return str(root).replace("\\", "/").casefold()


_ROOT_CACHE = {}   # 資料夾 → repo 根；一個資料夾只問 git 一次（對話常改上百個檔，但資料夾就那十幾個）


def _root_of_dir(d):
    key = str(d).casefold()
    with _CACHE_LOCK:
        if key in _ROOT_CACHE:
            return _ROOT_CACHE[key]
    root = repo_root(d)
    with _CACHE_LOCK:
        if len(_ROOT_CACHE) > 2000:
            _ROOT_CACHE.clear()
        _ROOT_CACHE[key] = root
    return root


def workspace(cwd, edited_paths, since_ts=None):
    """第 2～4 層。回 {"repo": cwd 所在 repo 根或 None, "edits_git": {檔案絕對路徑: git 狀態}, "repos": [每個牽涉到的 repo]}。
    每個被改的檔各自找它所在的 repo：對話的 cwd 常常不是 repo（在工作區根目錄開對話、改底下某個 repo 的檔），
    只認 cwd 那一個 repo 會把這些檔全標成「不在 git 裡」。"""
    roots = {}      # 比對鍵 → git 給的根
    cwd_root = repo_root(cwd) if cwd else None
    if cwd_root:
        roots[_norm(cwd_root)] = cwd_root
    by_root = {}    # 比對鍵 → {rel: 絕對路徑}
    for p in edited_paths:
        d = Path(p).parent
        root = _root_of_dir(str(d)) if d.is_dir() else None
        if not root:
            continue
        key = _norm(root)
        roots.setdefault(key, root)
        rel = _rel(root, p)
        if rel:
            by_root.setdefault(key, {})[rel] = p
    edits_git, repos = {}, []
    for key, root in roots.items():
        rels = by_root.get(key, {})
        status = {}
        out = _git(root, "status", "--porcelain", "--untracked-files=all")
        for line in (out or "").splitlines():
            if len(line) > 3:
                status[line[3:].strip().strip('"').replace("\\", "/")] = line[:2]
        numstat = {}
        if rels:
            out = _git(root, "diff", "--numstat", "HEAD", "--", *rels.keys())
            for line in (out or "").splitlines():
                parts = line.split("\t")
                if len(parts) == 3:
                    a, d, f = parts
                    numstat[f] = (int(a) if a.isdigit() else None, int(d) if d.isdigit() else None)
        for r, p in rels.items():
            st = status.get(r, "")
            edits_git[p] = {"root": root, "rel": r, "status": st.strip() or "clean", "untracked": st == "??",
                            "add": numstat.get(r, (None, None))[0], "del": numstat.get(r, (None, None))[1]}
        other = sorted(k for k in status if k not in rels)
        commits = []
        if since_ts:
            out = _git(root, "log", "--since=@%d" % int(since_ts), "-n", "20", "--format=%h%x09%ct%x09%s")
            for line in (out or "").splitlines():
                parts = line.split("\t", 2)
                if len(parts) == 3:
                    commits.append({"sha": parts[0], "ts": int(parts[1]), "subject": parts[2][:200]})
        repos.append({"root": root, "name": Path(root).name, "is_cwd": root == cwd_root, "n_edited": len(rels),
                      "other": {"count": len(other), "files": other[:30]}, "commits_since": commits})
    return {"repo": cwd_root, "edits_git": edits_git, "repos": repos}


def verify_commits(roots, commits):
    """Bash 輸出裡抓到的 sha，在牽涉到的任何一個 repo 查得到就算驗證過。"""
    out = []
    for c in commits:
        ok = any(_git(r, "cat-file", "-e", c["sha"] + "^{commit}") is not None for r in roots)
        out.append(dict(c, verified=ok))
    return out


def git_patch(root, rel):
    """第 2 層的整份差異（HEAD 對工作區）；未追蹤的新檔回整檔當成全部新增。"""
    out = _git(root, "diff", "HEAD", "--", rel)
    if out:
        return out
    st = _git(root, "status", "--porcelain", "--", rel) or ""
    if st.startswith("??"):
        try:
            text = Path(root, rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "新檔（尚未加進 git）\n" + "".join("+" + ln + "\n" for ln in text.splitlines())
    return ""


def session_patch(edit):
    """第 1 層：把對話紀錄裡的 structuredPatch 串成 unified diff 文字（按時間）。"""
    parts = []
    for item in edit.get("hunks", []):
        when = time.strftime("%m-%d %H:%M", time.localtime(item["ts"])) if item.get("ts") else "?"
        parts.append("### 修改於 %s" % when)
        for h in item["hunks"]:
            parts.append("@@ -%s,%s +%s,%s @@" % (h.get("oldStart"), h.get("oldLines"), h.get("newStart"), h.get("newLines")))
            parts.extend(h.get("lines") or [])
    return "\n".join(parts)


def changes(path, cwd):
    """GET /api/changes 的本體：四層＋測試＋新鮮度。"""
    s = scan_cached(path)
    ws = (workspace(cwd, s["edits"].keys(), s["first_ts"]) if (s["edits"] or s["commits"])
          else {"repo": None, "edits_git": {}, "repos": []})
    edits = []
    for p, e in sorted(s["edits"].items(), key=lambda kv: -(kv[1]["last_ts"] or 0)):
        g = ws["edits_git"].get(p)
        edits.append({"path": p, "root": g["root"] if g else None, "rel": g["rel"] if g else None, "name": Path(p).name,
                      "git": ({k: g[k] for k in ("status", "untracked", "add", "del")} if g else None),
                      "n": e["n"], "kinds": e["kinds"], "first_ts": e["first_ts"], "last_ts": e["last_ts"],
                      "created": e["created"], "exists": Path(p).exists(), "n_hunks": sum(len(h["hunks"]) for h in e["hunks"])})
    tests = [dict(t, freshness=freshness(t, s)) for t in s["tests"][-10:]]
    return {
        "cwd": cwd, "repo": ws["repo"], "repos": ws["repos"], "edits": edits,
        "commits": verify_commits([r["root"] for r in ws["repos"]], s["commits"]),
        "tests": tests,
        "last_edit_ts": s["last_edit_ts"],
    }


def allowed_patch_targets(path, cwd):
    """patch 端點只准看 changes() 列出的檔（本對話改過的＋各 repo 的工作區其他變更），不是任意路徑讀檔器。
    回 (changes 結果, {(repo 比對鍵, rel): repo 根})。"""
    c = changes(path, cwd)
    allowed = {}
    for e in c["edits"]:
        if e["root"] and e["rel"]:
            allowed[(_norm(e["root"]), e["rel"])] = e["root"]
    for r in c["repos"]:
        for rel in r["other"]["files"]:
            allowed[(_norm(r["root"]), rel)] = r["root"]
    return c, allowed
