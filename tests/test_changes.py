"""成果與變更（claude_chat/changes.py）：掃對話紀錄取修改紀錄／測試／提交，git 四層，新鮮度，路由的範圍限制。
git 的部分在 tmp 裡真的 git init 一個小 repo（這台有 git），沒有就整組 skip。"""
import shutil
import subprocess

import pytest
from helpers import TS, assistant, iso, tool_result, tool_use, write_jsonl

from claude_chat import changes as C
from claude_chat import store as S

T0 = 1_800_000_000


def edit_rec(uid, path, ts, hunks=None, create=False):
    """Edit/Write 的 tool_result：toolUseResult 自帶 filePath 與 structuredPatch（照真實紀錄的樣子）。"""
    r = tool_result(uid, "The file %s has been updated successfully." % path, ts=iso(ts))
    r["toolUseResult"] = {"filePath": path, "structuredPatch": hunks or [], "type": "create" if create else "update"}
    return r


def bash_rec(uid, text, ts, is_error=False):
    r = tool_result(uid, text, is_error=is_error, ts=iso(ts))
    r["toolUseResult"] = ("Error: " + text) if is_error else {"stdout": text, "stderr": "", "interrupted": False}
    return r


HUNK = {"oldStart": 1, "oldLines": 2, "newStart": 1, "newLines": 3, "lines": [" a", "+b", " c"]}


def transcript(tmp_path, repo=None):
    a = str((repo or tmp_path) / "app.py")
    new = str((repo or tmp_path) / "new.txt")
    recs = [
        assistant([tool_use("Edit", {"file_path": a, "old_string": "x", "new_string": "y"}, "u1")], ts=iso(T0),
                  cwd=str(repo or tmp_path)),
        edit_rec("u1", a, T0 + 1, [HUNK]),
        assistant([tool_use("Bash", {"command": "cd /x && python -m pytest -q"}, "u2")], ts=iso(T0 + 10)),
        bash_rec("u2", "....\n4 passed in 0.3s\n", T0 + 12),
        assistant([tool_use("Write", {"file_path": new, "content": "hi"}, "u3")], ts=iso(T0 + 20)),
        edit_rec("u3", new, T0 + 21, create=True),
        assistant([tool_use("Edit", {"file_path": a, "old_string": "y", "new_string": "z"}, "u4")], ts=iso(T0 + 30)),
        edit_rec("u4", a, T0 + 31, [HUNK]),
        assistant([tool_use("Bash", {"command": "pytest tests/ -q"}, "u5")], ts=iso(T0 + 40)),
        bash_rec("u5", "Exit code 1\nFAILED tests/test_a.py::t - boom\n1 failed, 3 passed in 0.2s\n", T0 + 42, is_error=True),
        assistant([tool_use("Bash", {"command": "git commit -m x"}, "u6")], ts=iso(T0 + 50)),
        bash_rec("u6", "[main abc1234] x\n 1 file changed\n", T0 + 52),
        assistant([tool_use("Bash", {"command": "ls"}, "u7")], ts=iso(T0 + 60)),   # 不是測試，不該被算進去
        bash_rec("u7", "a b c", T0 + 61),
    ]
    return write_jsonl(tmp_path / "s.jsonl", recs), a, new


def test_scan_collects_edits_tests_and_commits(tmp_path):
    f, a, new = transcript(tmp_path)
    s = C.scan_transcript(f)
    assert set(s["edits"]) == {a, new}
    assert s["edits"][a]["n"] == 2 and s["edits"][a]["kinds"] == ["Edit"] and len(s["edits"][a]["hunks"]) == 2
    assert s["edits"][new]["created"] is True and s["edits"][new]["kinds"] == ["Write"]
    assert [t["ok"] for t in s["tests"]] == [True, False]
    assert s["tests"][0]["counts"] == {"passed": 4} and s["tests"][0]["verdict"] == "4 過"
    assert s["tests"][1]["exit_code"] == 1 and s["tests"][1]["counts"] == {"failed": 1, "passed": 3}
    assert s["tests"][1]["verdict"] == "3 過、1 失敗"
    assert s["commits"] == [{"sha": "abc1234", "branch": "main", "subject": "x", "ts": T0 + 52}]
    assert s["last_edit_ts"] == T0 + 31


def test_freshness_is_stale_when_edits_follow_the_test_and_unverified_without_fingerprint(tmp_path):
    f, a, new = transcript(tmp_path)
    s = C.scan_transcript(f)
    assert C.freshness(s["tests"][0], s) == "stale"          # 第一次測試之後還改了兩次
    assert C.freshness(s["tests"][1], s) == "unverified"     # 最後一次測試之後沒改，但沒有指紋
    (tmp_path / "app.py").write_text("v1", encoding="utf-8")
    (tmp_path / "new.txt").write_text("hi", encoding="utf-8")
    fp = C.file_fingerprint([a, new])
    assert C.freshness(s["tests"][1], s, fp) == "current"
    (tmp_path / "app.py").write_text("v2", encoding="utf-8")   # 檔案在對話外被改了
    assert C.freshness(s["tests"][1], s, fp) == "stale"


def test_summary_and_cache_follow_the_file(tmp_path):
    f, a, new = transcript(tmp_path)
    sm = C.summary(f)
    assert sm["n_files"] == 2 and sm["n_edits"] == 3 and sm["test"]["ok"] is False and sm["test"]["freshness"] == "unverified"
    first = C.scan_cached(f)
    assert C.scan_cached(f) is first   # 檔沒變就用快取
    from helpers import append_jsonl
    append_jsonl(f, [assistant([tool_use("Edit", {"file_path": a}, "u9")], ts=iso(T0 + 90)), edit_rec("u9", a, T0 + 91)])
    assert C.scan_cached(f) is not first and C.summary(f)["n_edits"] == 4   # 檔長了就重掃
    assert C.summary(write_jsonl(tmp_path / "empty.jsonl", [assistant("hi")])) is None   # 沒改過東西就不給摘要


def test_session_patch_renders_hunks_in_order(tmp_path):
    f, a, new = transcript(tmp_path)
    s = C.scan_transcript(f)
    text = C.session_patch(s["edits"][a])
    assert text.count("@@ -1,2 +1,3 @@") == 2 and "+b" in text and text.index("修改於") < text.index("@@")
    assert C.session_patch(s["edits"][new]) == ""   # Write 整檔沒有差異塊


def test_parse_summary_knows_jest_and_check_cmd():
    assert C.parse_test_summary("Tests:       2 failed, 10 passed, 12 total\n") == {"failed": 2, "passed": 10}
    assert C.parse_test_summary("[3/3] pytest\nAll checks passed!\n") == {"checks": "ok"}
    assert C.parse_test_summary("CHECK FAILED\n") == {"checks": "failed"}
    assert C.parse_test_summary("nothing here") is None
    assert C.verdict_text(True, 0, None) == "指令成功，輸出裡沒有測試總結"
    assert C.verdict_text(False, 3, None) == "指令失敗（exit 3）"


def test_test_regex_matches_runners_but_not_lookalikes():
    yes = ["pytest -q", "cd x && python -m pytest tests", "npm test", "npm run test -- --watch", "npx vitest run",
           "go test ./...", "cargo test", "cmd //c check.cmd ui", "node --test",
           # 真實紀錄裡長這樣：絕對路徑的 python、包裝、變數、括號、第二行才是測試
           "cd /c/dev/claude-chat && C:/dev/claude-chat/.venv/Scripts/python.exe -m pytest tests -q",
           "cmd //c C:/dev/claude-chat/check.cmd ui 2>&1 | tail -8",
           "PY=C:/x/.venv/Scripts/python.exe; $PY -m pytest tests/test_a.py -q",
           "FOO=1 pytest -q", "(cd x && npm test)", "echo start\npytest -q\necho done"]
    # 字串、路徑、套件名裡出現的執行器名字不算：grep 一下不等於跑過測試
    no = ["ls pytest_cache", "cat README.md", "echo jest-like", "git log", "pip install pytest-xdist",
          'grep -n "^def \\|@pytest" tests/test_ui.py', "sed -n '1,60p' tests/test_ui.py",
          "git add tests/test_changes.py claude_chat/changes.py", 'echo "run pytest later"',
          'grep -v "^.*test" claude_chat/api.py | head']
    assert all(C.TEST_RE.search(c) for c in yes), [c for c in yes if not C.TEST_RE.search(c)]
    assert not any(C.TEST_RE.search(c) for c in no), [c for c in no if C.TEST_RE.search(c)]


def test_summary_prefers_latest_test_with_a_verdict_but_never_hides_a_later_failure(tmp_path):
    from helpers import append_jsonl
    recs = [assistant([tool_use("Bash", {"command": "pytest -q"}, "t1")], ts=iso(T0)), bash_rec("t1", "3 passed in 1s\n", T0 + 1),
            assistant([tool_use("Bash", {"command": "pytest -q -x | sed -n '1,5p'"}, "t2")], ts=iso(T0 + 10)),
            bash_rec("t2", "....\n", T0 + 11)]   # 成功但總結行被切掉：雜訊，不該蓋過前一次
    f = write_jsonl(tmp_path / "s.jsonl", recs)
    assert C.summary(f)["test"]["verdict"] == "3 過"
    append_jsonl(f, [assistant([tool_use("Bash", {"command": "pytest -q"}, "t3")], ts=iso(T0 + 20)),
                     bash_rec("t3", "Exit code 2\nboom\n", T0 + 21, is_error=True)])
    assert C.summary(f)["test"]["verdict"] == "指令失敗（exit 2）"   # 失敗不會被更早的綠燈蓋掉


# ---------- git 四層 ----------

git = shutil.which("git")


@pytest.fixture
def repo(tmp_path):
    if not git:
        pytest.skip("沒有 git")
    r = tmp_path / "repo"
    r.mkdir()

    def run(*a):
        subprocess.run([git, *a], cwd=r, check=True, capture_output=True)
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (r / "app.py").write_text("print(1)\n", encoding="utf-8")
    (r / "other.py").write_text("x = 1\n", encoding="utf-8")
    run("add", ".")
    run("commit", "-q", "-m", "init")
    return r


def test_workspace_separates_session_files_from_other_changes(tmp_path, repo):
    f, a, new = transcript(tmp_path, repo)
    (repo / "app.py").write_text("print(1)\nprint(2)\n", encoding="utf-8")   # 本對話改的
    (repo / "new.txt").write_text("hi", encoding="utf-8")                     # 本對話建的，未追蹤
    (repo / "other.py").write_text("x = 2\n", encoding="utf-8")               # 別人改的
    out = C.changes(f, str(repo))
    assert out["repo"] and out["repo"].replace("\\", "/").endswith("/repo")
    assert [r["name"] for r in out["repos"]] == ["repo"] and out["repos"][0]["is_cwd"] and out["repos"][0]["n_edited"] == 2
    g = {e["rel"]: e["git"] for e in out["edits"]}
    assert g["app.py"]["add"] == 1 and g["app.py"]["del"] == 0 and g["app.py"]["untracked"] is False
    assert g["new.txt"]["untracked"] is True
    assert out["repos"][0]["other"] == {"count": 1, "files": ["other.py"]}
    assert out["commits"] == [{"sha": "abc1234", "branch": "main", "subject": "x", "ts": T0 + 52, "verified": False}]
    assert [e["rel"] for e in out["edits"]] == ["app.py", "new.txt"]   # 最後改的排前面
    assert C.git_patch(out["repo"], "app.py").startswith("diff --git") and "+print(2)" in C.git_patch(out["repo"], "app.py")
    assert C.git_patch(out["repo"], "new.txt") == "新檔（尚未加進 git）\n+hi\n"
    assert C.git_patch(out["repo"], "other.py").startswith("diff --git")
    c, allowed = C.allowed_patch_targets(f, str(repo))
    assert {rel for _, rel in allowed} == {"app.py", "new.txt", "other.py"}
    assert set(allowed.values()) == {out["repo"]}


def test_edits_in_a_repo_other_than_cwd_still_get_git_status(tmp_path, repo):
    """對話在工作區根目錄開（不是 repo），改的是底下某個 repo 的檔：要找到那個 repo，不能標「不在 git 裡」。"""
    f, a, new = transcript(tmp_path, repo)
    (repo / "app.py").write_text("print(1)\nprint(2)\n", encoding="utf-8")
    (repo / "other.py").write_text("x = 2\n", encoding="utf-8")
    out = C.changes(f, str(tmp_path))          # cwd＝tmp_path，不是 repo
    assert out["repo"] is None
    assert [r["name"] for r in out["repos"]] == ["repo"] and out["repos"][0]["is_cwd"] is False
    g = {e["rel"]: e["git"] for e in out["edits"]}
    assert g["app.py"]["add"] == 1 and g["app.py"]["status"] == "M"
    assert out["repos"][0]["other"]["files"] == ["other.py"]
    c, allowed = C.allowed_patch_targets(f, str(tmp_path))
    assert allowed[(C._norm(repo), "app.py")].replace("\\", "/").endswith("/repo")


def test_no_repo_means_no_workspace_but_edits_still_listed(tmp_path):
    f, a, new = transcript(tmp_path)
    out = C.changes(f, str(tmp_path))
    assert out["repo"] is None and out["repos"] == []
    assert len(out["edits"]) == 2 and out["edits"][0]["rel"] is None and out["edits"][0]["git"] is None
    assert out["commits"][0]["verified"] is False


# ---------- store：測試落地指紋 ----------

def test_store_keeps_the_first_fingerprint_only(tmp_path):
    S.init(tmp_path / "state.sqlite")
    S.test_seen("u5", "sid", T0 + 42, {"a": "1"})
    S.test_seen("u5", "sid", T0 + 42, {"a": "2"})   # 第二次看到不覆蓋
    S.test_seen("u2", "sid", T0 + 12, None)
    assert S.test_fingerprints("sid") == {"u5": {"a": "1"}, "u2": None}
    S.init(tmp_path / "state2.sqlite")


# ---------- 路由：patch 只准列出的檔 ----------

def test_api_changes_and_patch_scope(tmp_path, repo, monkeypatch):
    from fastapi.testclient import TestClient

    from claude_chat import api as A
    f, a, new = transcript(tmp_path, repo)
    (repo / "app.py").write_text("print(1)\nprint(2)\n", encoding="utf-8")
    (repo / "secret.txt").write_text("nope", encoding="utf-8")
    S.init(tmp_path / "state.sqlite")
    monkeypatch.setattr(A, "find_room_file", lambda slug, sid: f)
    c = TestClient(A.app)
    out = c.get("/api/changes/slug/sid").json()
    assert [e["name"] for e in out["edits"]] == ["app.py", "new.txt"]
    assert out["tests"][-1]["verdict"] == "3 過、1 失敗"
    assert c.get("/api/changes/slug/sid/patch", params={"src": "git", "rel": "app.py"}).text.startswith("diff --git")
    # 範圍規則＝抽屜列出的檔：secret.txt 是未追蹤檔，會出現在「工作區其他變更」，所以看得到；
    # other.py 已提交且沒改動，抽屜不會列它，就不准看；路徑往上爬、指到別的 root 也不准。
    assert "secret.txt" in out["repos"][0]["other"]["files"]
    assert c.get("/api/changes/slug/sid/patch", params={"src": "git", "rel": "secret.txt"}).text.startswith("新檔")
    with_root = c.get("/api/changes/slug/sid/patch", params={"src": "git", "root": str(repo), "rel": "app.py"})
    assert with_root.text.startswith("diff --git")
    assert c.get("/api/changes/slug/sid/patch", params={"src": "git", "rel": "other.py"}).status_code == 404
    assert c.get("/api/changes/slug/sid/patch", params={"src": "git", "rel": "../x"}).status_code == 404
    assert c.get("/api/changes/slug/sid/patch", params={"src": "git", "root": str(tmp_path), "rel": "app.py"}).status_code == 404
    assert "+b" in c.get("/api/changes/slug/sid/patch", params={"src": "session", "path": a}).text
    assert c.get("/api/changes/slug/sid/patch", params={"src": "session", "path": str(repo / "secret.txt")}).status_code == 404
    # 紀錄裡的時間離現在很遠（固定在 2027-01，離現在不只 10 分鐘）：記進 test_runs 但不記指紋
    assert S.test_fingerprints("sid") == {"u2": None, "u5": None}
    S.init(tmp_path / "state2.sqlite")


def test_ts_helper_matches_real_format():
    assert iso(T0).endswith("Z") and TS.endswith("Z")
