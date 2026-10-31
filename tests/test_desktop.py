"""桌面 app 登錄檔：子對話的父對話、改名來源、/clear 前後紀錄檔的對照。"""
from claude_chat import desktop as D


def test_registry_reads_fork_and_side_session_parents(tmp_path, monkeypatch):
    import json
    d = tmp_path / "acct" / "org"
    d.mkdir(parents=True)
    files = {
        "local_p": {"cliSessionId": "cli-p", "title": "父對話"},
        "local_f": {"cliSessionId": "cli-f", "title": "父對話 (fork)", "forkedFromSessionId": "local_p"},
        "local_u": {"cliSessionId": "cli-u", "spawnedFrom": {"sessionId": "local_p", "userFork": True}},
        "local_s": {"cliSessionId": "cli-s", "spawnedFrom": {"sessionId": "local_p", "sideSession": True}},
    }
    for name, body in files.items():
        (d / (name + ".json")).write_text(json.dumps(dict(body, sessionId=name)), "utf-8")
    monkeypatch.setattr(D, "_desktop_reg_dirs", lambda: [tmp_path])
    monkeypatch.setattr(D, "_desktop_reg_cache", {"at": 0, "data": {}})
    monkeypatch.setattr(D, "_desktop_reg_files", {})
    reg = D.desktop_registry()
    assert (reg["cli-p"]["parent"], reg["cli-p"]["kind"]) == ("", "")
    assert (reg["cli-f"]["parent"], reg["cli-f"]["kind"]) == ("local_p", "fork")
    assert (reg["cli-u"]["parent"], reg["cli-u"]["kind"]) == ("local_p", "fork")
    assert (reg["cli-s"]["parent"], reg["cli-s"]["kind"]) == ("local_p", "side")


def test_registry_remembers_which_desktop_session_each_transcript_belonged_to(tmp_path, monkeypatch):
    import json
    d = tmp_path / "acct" / "org"
    d.mkdir(parents=True)
    f = d / "local_p.json"
    monkeypatch.setattr(D, "_desktop_reg_dirs", lambda: [tmp_path])
    for cli in ("cli-1", "cli-2"):   # 同一個桌面對話 /clear 一次：紀錄檔從 cli-1 換到 cli-2
        f.write_text(json.dumps({"sessionId": "local_p", "cliSessionId": cli, "titleSource": "user"}), "utf-8")
        monkeypatch.setattr(D, "_desktop_reg_cache", {"at": 0, "data": {}})
        monkeypatch.setattr(D, "_desktop_reg_files", {})
        assert D.desktop_registry()[cli]["renamed"] is True
    assert json.loads(D.GENS_FILE.read_text("utf-8")) == {"cli-1": "local_p", "cli-2": "local_p"}
