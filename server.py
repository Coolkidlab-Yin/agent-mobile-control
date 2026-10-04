# -*- coding: utf-8 -*-
"""claude-chat — 用聊天室介面操作 Claude Code sessions（手機經 Tailscale 使用）。

聊天室 = ~/.claude/projects/<slug>/<session-id>.jsonl
送訊息 = 對該 session 跑 `claude -p --resume <sid>`，stream-json 事件經 SSE 推給前端。
只綁 127.0.0.1 與 Tailscale IP，家用區網與外網碰不到。

這個檔只是進入點（排程工作 claude-chat-server 跑的就是它，位置不能動）。程式本體在 claude_chat/：
  config    路徑、設定檔、常數、引擎表      jsonl     對話紀錄解析（純函式）
  rooms     聊天室清單、桌面登錄對齊          history   歷史訊息、尾讀
  runs      進行中工作的登記表               runner    跑 claude -p / codex / API 引擎
  peer      直送桌面行程的 pipe 通道          perm      授權卡（先問我模式、桌面確認框）
  bgtasks   背景任務偵測                     usage     方案額度與 token 統計
  search    全文搜尋                         desktop   桌面 app 登錄檔、claude:// 連結
  bridge    hook / MCP 設定檔落地            procs     Windows 行程查詢
  api       FastAPI 路由層（/api/*、靜態頁、前端 JS 組裝）
改完程式先跑 check.cmd，再用 restart-server.cmd 重啟。
"""
import asyncio
import logging
import sys

import uvicorn

from claude_chat.api import app
from claude_chat.config import CLAUDE_ARGV, LOG_FILE, PORT, bind_hosts, log

# ---------- 進入點 ----------

def main():
    LOG_FILE.parent.mkdir(exist_ok=True)
    logging.basicConfig(filename=str(LOG_FILE), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        encoding="utf-8")
    # pythonw 下 stdout/stderr 是 None，接到 log 檔避免噴錯
    if sys.stdout is None or sys.stderr is None:
        f = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = f
        if sys.stderr is None:
            sys.stderr = f
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    hosts = bind_hosts()
    log.info("claude-chat starting on %s:%s (claude=%s)", hosts, PORT, CLAUDE_ARGV)

    async def serve_all():
        servers = []
        for h in hosts:
            cfg = uvicorn.Config(app, host=h, port=PORT,
                                 log_config=None, access_log=False)
            srv = uvicorn.Server(cfg)
            servers.append(srv.serve())
        await asyncio.gather(*servers)

    try:
        asyncio.run(serve_all())
    except KeyboardInterrupt:
        pass
    except OSError as e:
        log.error("port busy or bind failed: %s", e)


if __name__ == "__main__":
    main()
