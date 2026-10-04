# -*- coding: utf-8 -*-
"""hook / MCP 設定檔落地：手機 run 的提問通道（askuser-mcp.js）與「先問我」模式的授權 hook（perm-bridge.js）。"""
import json
import shutil
from pathlib import Path

from .config import BASE

# ---------- AskUserQuestion 手機橋接 ----------
# claude -p 的工具清單裡「沒有」內建 AskUserQuestion，模型根本無從問使用者選擇題。
# 解法：每個手機 run 掛一個自製 MCP 工具 mcp__chat__ask_user 當提問通道——
# 模型呼叫它 → askuser-mcp.js POST /api/ask → 手機顯示選項卡 → 答案當工具結果回去。
ASK_MCP_JS = BASE / "askuser-mcp.js"
ASK_MCP_CONFIG = BASE / "ask-mcp-config.json"
ASK_SYSTEM_PROMPT = (
    "你正在使用者的手機聊天介面（claude-chat）裡執行。需要問使用者選擇題"
    "（釐清模糊需求、做決定、在多個做法中挑一個）時，呼叫 mcp__chat__ask_user 工具："
    "手機會顯示可點選的選項卡，使用者的選擇會當成工具結果回傳給你。"
    "這個工具可能是 deferred（只列出名字、沒有參數 schema）："
    "先用 ToolSearch 查 select:mcp__chat__ask_user 把 schema 載進來，再呼叫它。"
    "不要呼叫 AskUserQuestion：CLI 2.1.283 起 -p session 的那個工具被官方停用，"
    "呼叫只會拿到「nobody in this session can answer it」，攔它的 PreToolUse hook 也不會被觸發。"
    "也不要用純文字列出選項乾等回覆——使用者在手機上只能點卡片。"
)


def _write_ask_mcp_config():
    """把 MCP 設定檔落地（node 路徑因機器而異，啟動時現算）。"""
    if not ASK_MCP_JS.exists():
        return False
    node = shutil.which("node")
    if not node:
        pf = Path(r"C:\Program Files\nodejs\node.exe")
        node = str(pf) if pf.exists() else "node"
    cfg = {"mcpServers": {"chat": {"command": node, "args": [str(ASK_MCP_JS)]}}}
    ASK_MCP_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


ASK_MCP_READY = _write_ask_mcp_config()

# ---------- 逐項授權（「先問我」模式）手機橋接 ----------
# -p 在 default 權限模式下，沒被允許的工具會直接被拒。掛一個 PreToolUse hook：
# 要動手（Bash/Edit/Write…）前先 POST /api/perm → 手機跳「允許／拒絕」卡 → 決定當 hook 結果。
# 這份 settings 只在 ask 模式用 --settings 帶進去，桌面 app 與一般 CLI 完全不受影響。
PERM_BRIDGE_JS = BASE / "perm-bridge.js"
PERM_SETTINGS = BASE / "perm-settings.json"
PERM_MATCHER = "Bash|Edit|Write|MultiEdit|NotebookEdit|WebFetch|mcp__.*"


def _write_perm_settings():
    if not PERM_BRIDGE_JS.exists():
        return False
    node = shutil.which("node")
    if not node:
        pf = Path(r"C:\Program Files\nodejs\node.exe")
        node = str(pf) if pf.exists() else "node"
    cfg = {"hooks": {"PreToolUse": [{
        "matcher": PERM_MATCHER,
        "hooks": [{"type": "command", "command": f'"{node}" "{PERM_BRIDGE_JS}"', "timeout": 600}],
    }]}}
    PERM_SETTINGS.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


PERM_READY = _write_perm_settings()
