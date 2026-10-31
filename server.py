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

LISTENER_CHECK_S = 30


def _config(host, lifespan):
    return uvicorn.Config(app, host=host, port=PORT, log_config=None, access_log=False, lifespan=lifespan)


async def _refused(host):
    """自己連一下：被拒＝這個位址已經沒人在聽。慢、逾時、網路怪都不算（伺服器忙時系統照樣先接下連線）。"""
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, PORT), 5)
    except ConnectionRefusedError:
        return True
    except (OSError, asyncio.TimeoutError):
        return False
    w.close()
    return False


async def _serve(host, lifespan="auto"):
    """跑 uvicorn；監聽被關掉就在同一個位址補開一個（啟動程序不重跑）。
    Windows 的 Proactor 在接連線時遇到任何 OSError（例如客戶端排隊時先斷線的 WinError 64）就把監聽 socket 關掉、
    不再重開（asyncio/proactor_events.py）：那個位址從此連不上，行程卻還活著，看門狗看不出來。"""
    task = asyncio.ensure_future(uvicorn.Server(_config(host, lifespan)).serve())
    while not task.done():
        await asyncio.wait({task}, timeout=LISTENER_CHECK_S)
        if not task.done() and await _refused(host):
            log.warning("%s:%s 的監聽被關掉了，補開一個", host, PORT)
            asyncio.ensure_future(uvicorn.Server(_config(host, "off")).serve())
    await task

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
        await asyncio.gather(*(_serve(h) for h in hosts))

    try:
        asyncio.run(serve_all())
    except KeyboardInterrupt:
        pass
    except OSError as e:
        log.error("port busy or bind failed: %s", e)


if __name__ == "__main__":
    main()
