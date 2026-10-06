"""執行器：claude -p / codex exec / OpenAI 相容 API。子行程與網路全部換成假的，驗三件事：
指令怎麼組（旗標、環境變數、stdin）、串流事件怎麼轉成手機看的事件、收尾有沒有把登記清掉。"""
import asyncio
import io
import json
import urllib.error

import pytest
from helpers import dump

from claude_chat import runner as RN
from claude_chat.config import CONFIG
from claude_chat.runs import BY_SESSION, RUNS, Run

SID = "eeeeeeee-0000-0000-0000-000000000005"


# ---------- 假子行程 ----------

class _Stream:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    async def readline(self):
        return self.chunks.pop(0) if self.chunks else b""

    async def read(self):
        data = b"".join(self.chunks)
        self.chunks = []
        return data


class _Stdin:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, b):
        self.data += b

    async def drain(self):
        pass

    def close(self):
        self.closed = True


class FakeProc:
    def __init__(self, out_lines, err=b"", rc=0):
        self.stdout = _Stream(out_lines)
        self.stderr = _Stream([err])
        self.stdin = _Stdin()
        self.returncode = rc

    async def wait(self):
        return self.returncode


@pytest.fixture
def spawn(monkeypatch):
    """換掉 asyncio.create_subprocess_exec：記下 argv/env/cwd，吐出事先排好的 stdout 行。"""
    calls = []
    box = {"out": [], "err": b"", "rc": 0, "raise": None}

    async def fake(*argv, **kw):
        if box["raise"]:
            raise box["raise"]
        calls.append({"argv": list(argv), "env": kw.get("env"), "cwd": kw.get("cwd")})
        proc = FakeProc(box["out"], box["err"], box["rc"])
        calls[-1]["proc"] = proc
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)

    async def _no_gc(run_id, delay=0):
        RUNS.pop(run_id, None)
    monkeypatch.setattr(RN, "_gc_run", _no_gc)
    monkeypatch.setattr(RN, "remember_app_sid", lambda sid: box.setdefault("remembered", []).append(sid))
    monkeypatch.setattr(RN, "desktop_open", lambda sid: box.setdefault("desktop_open", []).append(sid) or "imported")
    monkeypatch.setitem(CONFIG, "desktop_sync", "manual")
    box["calls"] = calls
    yield box
    BY_SESSION.clear()
    RUNS.clear()


def lines(*recs):
    return [dump(r).encode("utf-8") for r in recs]


def mk_run(sid=SID, run_id="r1"):
    run = Run(run_id, "C--work-p", sid, "C:\\work\\p")
    RUNS[run_id] = run
    if sid:
        BY_SESSION[sid] = run_id
    return run


def kinds(run):
    return [e.get("kind") or e.get("role") for e in run.events]


# ---------- claude -p ----------

def test_run_claude_builds_argv_env_and_feeds_stdin(spawn, monkeypatch):
    monkeypatch.setattr(RN, "CLAUDE_ARGV", ["claude.exe"])
    monkeypatch.setattr(RN, "ASK_MCP_READY", True)
    monkeypatch.setattr(RN, "PERM_READY", True)
    spawn["out"] = lines({"type": "result", "subtype": "success", "session_id": SID})
    run = mk_run()
    asyncio.run(RN.run_claude(run, "你好", "ask", extra_args=["--model", "claude-opus-5"]))
    c = spawn["calls"][0]
    argv = c["argv"]
    assert argv[:5] == ["claude.exe", "-p", "--output-format", "stream-json", "--verbose"]
    assert argv[-2:] == ["--resume", SID] and "--model" in argv
    assert "--permission-mode" not in argv and "--dangerously-skip-permissions" not in argv   # ask 模式走 default
    assert argv[argv.index("--settings") + 1] == str(RN.PERM_SETTINGS)
    assert argv[argv.index("--mcp-config") + 1] == str(RN.ASK_MCP_CONFIG)
    assert argv[argv.index("--allowedTools") + 1] == "mcp__chat__ask_user"
    assert c["cwd"] == run.cwd
    assert c["env"]["CLAUDE_CHAT_RUN_ID"] == "r1" and c["env"]["CLAUDE_CHAT_PORT"] == str(RN.PORT)
    assert c["env"]["MCP_TOOL_TIMEOUT"] == "600000"
    assert c["proc"].stdin.data == "你好".encode("utf-8") and c["proc"].stdin.closed


@pytest.mark.parametrize("mode,flag", [("auto", "--dangerously-skip-permissions"), ("edits", "acceptEdits"),
                                       ("plan", "plan"), ("nonsense", "plan")])
def test_run_claude_permission_flags_per_mode(spawn, monkeypatch, mode, flag):
    monkeypatch.setattr(RN, "ASK_MCP_READY", False)
    monkeypatch.setattr(RN, "PERM_READY", False)
    spawn["out"] = lines({"type": "result", "subtype": "success", "session_id": SID})
    asyncio.run(RN.run_claude(mk_run(), "x", mode))
    argv = spawn["calls"][0]["argv"]
    assert flag in argv and "--settings" not in argv and "--mcp-config" not in argv


def test_run_claude_streams_events_and_registers_new_session(spawn):
    spawn["out"] = lines(
        {"type": "system", "subtype": "init", "session_id": SID},
        {"type": "assistant", "message": {
            "role": "assistant", "model": "claude-opus-5",
            "content": [{"type": "text", "text": "回覆"},
                        {"type": "tool_use", "name": "Bash", "input": {"command": "dir"}, "id": "t1"}],
            "usage": {"input_tokens": 1000, "cache_read_input_tokens": 2000}}},
        {"type": "user", "message": {"role": "user",
                                     "content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True}]}},
        {"type": "result", "subtype": "success", "session_id": SID, "duration_ms": 1234},
    )
    run = mk_run(sid=None)   # 手機開的新對話：sid 要從 init 事件拿
    run.is_new = True
    asyncio.run(RN.run_claude(run, "x", "auto"))
    assert run.sid == SID and spawn["remembered"] == [SID]
    assert kinds(run) == ["init", "text", "tool", "ctx", "tool_ok", "done"]
    assert run.events[0]["sid"] == SID
    assert run.events[1]["text"] == "回覆"
    assert run.events[2]["tool"] == "Bash"
    assert run.events[3]["tokens"] == 3000 and run.events[3]["window"] > 3000
    assert run.events[4] == {"kind": "tool_ok", "tool_use_id": "t1", "ok": False}
    assert run.events[5] == {"kind": "done", "ok": True, "sid": SID, "error": "", "duration_ms": 1234}
    assert SID not in BY_SESSION, "做完要把登記清掉，不然房間一直顯示工作中"
    assert "desktop_open" not in spawn   # desktop_sync=manual 不自動登錄桌面


def test_run_claude_auto_desktop_sync_only_for_new_sessions(spawn, monkeypatch):
    monkeypatch.setitem(CONFIG, "desktop_sync", "auto")
    spawn["out"] = lines({"type": "system", "subtype": "init", "session_id": SID},
                         {"type": "result", "subtype": "success", "session_id": SID})
    run = mk_run(sid=None)
    asyncio.run(RN.run_claude(run, "x", "auto"))
    assert spawn["desktop_open"] == [SID]
    old = mk_run(sid=SID, run_id="r2")   # 續聊的對話本來就在桌面，不重複登錄
    asyncio.run(RN.run_claude(old, "x", "auto"))
    assert spawn["desktop_open"] == [SID]


def test_run_claude_reports_error_result_and_missing_result(spawn):
    spawn["out"] = lines({"type": "result", "subtype": "error_max_turns", "is_error": True,
                          "result": "跑太多輪了", "session_id": SID})
    run = mk_run()
    asyncio.run(RN.run_claude(run, "x", "auto"))
    assert run.events[-1]["ok"] is False and run.events[-1]["error"] == "跑太多輪了"

    spawn["out"], spawn["err"], spawn["rc"] = [], "stderr 最後幾行".encode("utf-8"), 1
    run = mk_run(run_id="r2")
    asyncio.run(RN.run_claude(run, "x", "auto"))
    assert run.events[-1]["ok"] is False and run.events[-1]["error"] == "stderr 最後幾行"

    spawn["err"] = b""
    run = mk_run(run_id="r3")
    asyncio.run(RN.run_claude(run, "x", "auto"))
    assert run.events[-1]["error"] == "claude 結束了但沒有回傳結果（exit 1）"


def test_run_claude_spawn_failure_still_sends_done_and_cleans_up(spawn):
    spawn["raise"] = FileNotFoundError("claude.exe 不見了")
    run = mk_run()
    asyncio.run(RN.run_claude(run, "x", "auto"))
    assert run.events == [{"kind": "done", "ok": False, "sid": SID, "error": "FileNotFoundError: claude.exe 不見了"}]
    assert SID not in BY_SESSION and "r1" not in RUNS


# ---------- codex exec ----------

def test_run_codex_argv_new_vs_resume_and_sandbox(spawn, monkeypatch):
    monkeypatch.setattr(RN, "CODEX_EXE", "codex.cmd")
    spawn["out"] = lines({"type": "turn.completed", "usage": {"input_tokens": 1}})
    asyncio.run(RN.run_codex(mk_run(sid=None), "x", "auto"))
    argv = spawn["calls"][0]["argv"]
    assert argv[:7] == ["codex.cmd", "exec", "--json", "--skip-git-repo-check", "-C", "C:\\work\\p", "-s"]
    assert argv[7:] == ["danger-full-access", "--dangerously-bypass-approvals-and-sandbox", "-"]
    asyncio.run(RN.run_codex(mk_run(run_id="r2"), "x", "plan"))
    argv = spawn["calls"][1]["argv"]
    assert argv[6:] == ["-s", "read-only", "resume", SID, "-"]


def test_run_codex_streams_and_finishes(spawn):
    spawn["out"] = lines(
        {"type": "thread.started", "thread_id": SID},
        {"type": "turn.started", "model_context_window": 200000},
        {"type": "item.completed", "item": {"type": "command_execution", "command": "dir /b", "exit_code": 2}},
        {"type": "item.completed", "item": {"type": "file_change", "path": "a.py"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "做完了"}},
        {"type": "turn.completed", "usage": {"input_tokens": 1500, "cached_input_tokens": 500}},
    )
    run = mk_run(sid=None)
    asyncio.run(RN.run_codex(run, "x", "auto"))
    assert run.sid == SID
    assert kinds(run) == ["init", "tool", "tool", "text", "ctx", "done"]
    assert run.events[1]["tool"] == "Shell" and run.events[1]["ok"] is False   # exit_code 2
    assert run.events[2]["tool"] == "改檔案" and run.events[2]["ok"] is True
    assert run.events[4] == {"kind": "ctx", "tokens": 2000, "window": 200000, "pct": 1}
    assert run.events[5]["ok"] is True and SID not in BY_SESSION


def test_run_codex_failure_paths(spawn):
    spawn["out"] = lines({"type": "turn.failed", "error": {"message": "額度用完"}})
    run = mk_run()
    asyncio.run(RN.run_codex(run, "x", "auto"))
    assert run.events[-1] == {"kind": "done", "ok": False, "sid": SID, "error": "額度用完"}

    spawn["out"], spawn["err"], spawn["rc"] = [], b"", 3
    run = mk_run(run_id="r2")
    asyncio.run(RN.run_codex(run, "x", "auto"))
    assert run.events[-1]["error"] == "Codex 結束但沒有回覆（exit 3）"


# ---------- OpenAI 相容 API ----------

class _Resp:
    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self.lines)


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    monkeypatch.setattr(RN, "API_CHATS", tmp_path / "api-chats")
    keys = {"grok": {"key": "k-1"}}
    monkeypatch.setattr(RN, "load_keys", lambda: keys)

    async def _no_gc(run_id, delay=0):
        RUNS.pop(run_id, None)
    monkeypatch.setattr(RN, "_gc_run", _no_gc)
    box = {"keys": keys, "requests": []}

    def set_response(lines=None, error=None):
        def fake_urlopen(req, timeout=0):
            box["requests"].append(req)
            if error:
                raise error
            return _Resp(lines)
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    box["set"] = set_response
    yield box
    BY_SESSION.clear()
    RUNS.clear()


def sse(*pieces):
    out = [b"data: " + json.dumps({"choices": [{"delta": {"content": p}}]}).encode() + b"\n" for p in pieces]
    return out + [b"\n", b"data: [DONE]\n"]


def test_run_api_without_key_explains_where_to_set_it(api_env):
    api_env["keys"].clear()
    run = mk_run(sid=None)
    asyncio.run(RN.run_api(run, "hi", "grok", None))
    assert run.events[-1]["ok"] is False and "還沒設定 Grok 的 API key" in run.events[-1]["error"]


def test_run_api_streams_reply_persists_chat_and_sends_history(api_env):
    api_env["set"](sse("你", "好"))
    run = mk_run(sid=None)
    asyncio.run(RN.run_api(run, "第一句", "grok", None))
    assert run.sid and kinds(run) == ["init", "text", "done"] and run.events[1]["text"] == "你好"
    f = RN.API_CHATS / "grok" / (run.sid + ".jsonl")
    recs = [json.loads(line) for line in f.read_text("utf-8").splitlines()]
    assert [(r["role"], r["content"]) for r in recs] == [("user", "第一句"), ("assistant", "你好")]
    assert recs[0]["model"] == RN.ENGINES["grok"]["model"]

    api_env["set"](sse("再"))
    asyncio.run(RN.run_api(mk_run(sid=run.sid, run_id="r2"), "第二句", "grok", "grok-3"))
    req = api_env["requests"][-1]
    body = json.loads(req.data)
    assert body["model"] == "grok-3" and body["stream"] is True
    assert [m["content"] for m in body["messages"]] == ["第一句", "你好", "第二句"]   # 之前的對話一起送
    assert req.get_header("Authorization") == "Bearer k-1"
    assert req.full_url == RN.ENGINES["grok"]["base"] + "/chat/completions"


def test_run_api_http_errors_are_explained(api_env):
    api_env["set"](error=urllib.error.HTTPError("u", 401, "nope", {}, io.BytesIO(b"bad key")))
    run = mk_run(sid=None)
    asyncio.run(RN.run_api(run, "hi", "grok", None))
    assert run.events[-1]["ok"] is False and "API key 被拒絕" in run.events[-1]["error"]
    api_env["set"](error=urllib.error.HTTPError("u", 500, "boom", {}, io.BytesIO(b"server down")))
    run = mk_run(sid=None, run_id="r2")
    asyncio.run(RN.run_api(run, "hi", "grok", None))
    assert run.events[-1]["error"] == "HTTP 500：server down"
    assert not BY_SESSION
