"""直送桌面通道：啟動時清掉死掉的 claude-chat 登記檔（硬砍重啟會留下來），別人的登記不碰。"""
import json

from claude_chat import peer as PEER


def test_sweep_removes_only_dead_claude_chat_registrations(tmp_path, monkeypatch):
    monkeypatch.setattr(PEER, "LIVE_DIR", tmp_path)
    monkeypatch.setattr(PEER, "_pid_alive", lambda pid: pid == 1)
    for pid, entry in ((1, "claude-chat"), (2, "claude-chat"), (3, "cli")):
        (tmp_path / f"{pid}.json").write_text(json.dumps({"pid": pid, "entrypoint": entry}), "utf-8")
        (tmp_path / f"{pid}.abc.key").write_text("{}", "utf-8")
    (tmp_path / "bad.json").write_text("not json", "utf-8")
    PEER._sweep_dead_peers()
    # 1 還活著留著；2 死了連 .key 一起清；3 是別人的（就算 pid 死了）不動；壞掉的檔跳過
    assert sorted(p.name for p in tmp_path.iterdir()) == ["1.abc.key", "1.json", "3.abc.key", "3.json", "bad.json"]
