# -*- coding: utf-8 -*-
"""路徑、設定檔（config.json）、常數、引擎表。沒有邏輯，只有「東西在哪、值是多少」。"""
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

HOME = Path.home()
PROJECTS_DIR = HOME / ".claude" / "projects"
LIVE_DIR = HOME / ".claude" / "sessions"
BASE = Path(__file__).resolve().parent.parent   # 專案根目錄（claude_chat/ 的上一層）
STATIC = BASE / "static"
LOG_FILE = BASE / "logs" / "server.log"
PORT = 8899
TAILSCALE_EXE = r"C:\Program Files\Tailscale\tailscale.exe"
TAILSCALE_WAIT = 120   # 開機時最多等 Tailscale 幾秒


log = logging.getLogger("claude-chat")


CONFIG_FILE = BASE / "config.json"

# 預設一律走最保守的設定：只綁 127.0.0.1、AI 不能自己動手、只讀自己產生的檔案。
# 要放寬就在 config.json 明確打開（見 README 的 Configuration）。
CONFIG_DEFAULTS = {
    "bind_tailscale": False,   # True = 也綁 Tailscale IP，手機才連得到
    "auth_token": "",          # 綁網路時強烈建議設；空字串 = 不驗證
    "default_mode": "plan",    # plan / edits / auto，auto 等於讓 AI 免詢問執行指令
    "extra_file_roots": [],    # /api/file 額外開放的資料夾，預設只開上傳目錄
    "allow_home_reads": False, # True = /api/file 可讀整個家目錄（含憑證，危險）
    "desktop_sync": "manual",  # auto = 手機開的新對話做完第一輪就自動登錄進桌面 app；manual = 只在按鈕按下時；off = 不用
}


def load_config():
    cfg = dict(CONFIG_DEFAULTS)
    try:
        user = json.loads(CONFIG_FILE.read_text("utf-8"))
        if isinstance(user, dict):
            cfg.update({k: v for k, v in user.items() if k in CONFIG_DEFAULTS})
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("config.json 讀不到或格式壞掉，改用預設值：%s", e)
    env_token = os.environ.get("CLAUDE_CHAT_TOKEN", "").strip()
    if env_token:
        cfg["auth_token"] = env_token
    return cfg


CONFIG = load_config()


def bind_hosts():
    """預設只綁 127.0.0.1。要讓手機連得到才打開 bind_tailscale。"""
    hosts = ["127.0.0.1"]
    if not CONFIG["bind_tailscale"]:
        return hosts
    # 登入時排程比 Tailscale 先起來，第一次查會查不到 → 最多等 2 分鐘再放棄
    # （09-02 中招：18:40 重新登入後只綁了 127.0.0.1，手機整晚連不上）
    deadline = time.time() + TAILSCALE_WAIT
    while True:
        try:
            out = subprocess.run([TAILSCALE_EXE, "ip", "-4"], capture_output=True,
                                 text=True, timeout=10,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
            ip = out.stdout.strip().splitlines()[0].strip() if out.stdout.strip() else ""
            if ip.startswith("100."):
                hosts.append(ip)
                return hosts
        except Exception as e:
            log.warning("查 Tailscale IP 失敗：%s", e)
        if time.time() >= deadline:
            log.warning("等了 %d 秒還是找不到 Tailscale IP，只綁 127.0.0.1", TAILSCALE_WAIT)
            return hosts
        time.sleep(3)


MAX_ROOMS = 250
MAX_CONCURRENT_RUNS = 4
HEAD_BYTES = 128 * 1024
TAIL_BYTES = 256 * 1024
MAX_HISTORY_BYTES = 64 * 1024 * 1024


# ---------- claude 執行檔解析 ----------

def resolve_claude_cmd():
    """優先直接用 node + cli.js（避開 .cmd 的 cmd.exe 引號地雷）。"""
    npm = HOME / "AppData" / "Roaming" / "npm"
    exe = npm / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    if exe.exists():
        return [str(exe)]
    cli = npm / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
    node = shutil.which("node")
    if not node:
        pf = Path(r"C:\Program Files\nodejs\node.exe")
        node = str(pf) if pf.exists() else None
    if node and cli.exists():
        return [node, str(cli)]
    cmd = shutil.which("claude.cmd") or str(npm / "claude.cmd")
    return ["cmd.exe", "/d", "/c", cmd]


CLAUDE_ARGV = resolve_claude_cmd()


CODEX_EXE = shutil.which("codex.cmd") or str(HOME / "AppData" / "Roaming" / "npm" / "codex.cmd")
CODEX_SESSIONS = HOME / ".codex" / "sessions"
API_CHATS = BASE / "api-chats"
KEYS_FILE = BASE / "api-keys.json"

# 引擎：cli = 本機 agent（能讀寫檔案跑指令）；api = 純聊天（貼 key 就能用）
ENGINES = {
    "claude": {"label": "Claude Code", "kind": "cli", "icon": "✳", "note": "能改檔案、跑指令"},
    "codex":  {"label": "Codex", "kind": "cli", "icon": "◆", "note": "OpenAI 的 agent，能改檔案、跑指令"},
    "grok":   {"label": "Grok", "kind": "api", "icon": "𝕏", "base": "https://api.x.ai/v1",
               "model": "grok-4-latest", "note": "純聊天，需要 x.ai 的 API key"},
    "gemini": {"label": "Gemini", "kind": "api", "icon": "✦",
               "base": "https://generativelanguage.googleapis.com/v1beta/openai",
               "model": "gemini-2.5-pro", "note": "純聊天，需要 Google AI Studio 的 API key"},
    "openai": {"label": "ChatGPT", "kind": "api", "icon": "◎", "base": "https://api.openai.com/v1",
               "model": "gpt-5", "note": "純聊天，需要 OpenAI 的 API key"},
    "deepseek": {"label": "DeepSeek", "kind": "api", "icon": "◇", "base": "https://api.deepseek.com/v1",
                 "model": "deepseek-chat", "note": "純聊天，需要 DeepSeek 的 API key"},
    "openrouter": {"label": "OpenRouter", "kind": "api", "icon": "⇄", "base": "https://openrouter.ai/api/v1",
                   "model": "openai/gpt-5", "note": "一把 key 通多家模型，型號可自己填"},
}
API_SLUG = {e: "api-" + e for e, d in ENGINES.items() if d["kind"] == "api"}
SLUG_ENGINE = {v: k for k, v in API_SLUG.items()}
SLUG_ENGINE["codex"] = "codex"


def load_keys():
    try:
        return json.loads(KEYS_FILE.read_text("utf-8"))
    except Exception:
        return {}


def save_keys(d):
    KEYS_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), "utf-8")


PERMISSION_FLAGS = {
    "auto": ["--dangerously-skip-permissions"],
    "edits": ["--permission-mode", "acceptEdits"],
    "plan": ["--permission-mode", "plan"],
    "ask": [],
}


# ---------- 本程式自己的資料檔（都在專案根目錄，全部列在 .gitignore） ----------
SNAP_FILE = BASE / "desktop-sessions.json"
MATCH_TOLERANCE = 180.0
WEB_ARCHIVE = BASE / "web-archive.json"
TRASH_DIR = BASE / "trash"
APP_SESSIONS = BASE / "app-sessions.json"
TITLES_FILE = BASE / "web-titles.json"   # 手機上改的聊天室名字 {sid: title}


BUSY_WINDOW = 15.0   # jsonl 幾秒內有寫入就當「桌面正在工作」


# key 是手機 UI 存的短名；值是 CLI --model 認得的完整 ID。清單與說明在 static/js/state.js 的 MODEL_LIST，兩邊要一起改。
MODELS = {
    "fable": "claude-fable-5-1",
    "fable5": "claude-fable-5",
    "opus55": "claude-opus-5-5",
    "opus": "claude-opus-5",
    "opus48": "claude-opus-4-8",
    "opus47": "claude-opus-4-7",
    "opus46": "claude-opus-4-6",
    "sonnet": "claude-sonnet-5",
    "sonnet46": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
}
EFFORTS = {"low", "medium", "high", "max"}


UPLOAD_DIR = BASE / "uploads"
UPLOAD_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp",
               # 桌面 app 能附 PDF/文件，手機也開放這幾種（AI 用 Read 工具讀）
               ".pdf", ".txt", ".md", ".csv", ".json"}
DOC_EXTS = {".pdf", ".txt", ".md", ".csv", ".json"}
UPLOAD_MAX = 25 * 1024 * 1024
UPLOAD_QUOTA = 2 * 1024 * 1024 * 1024   # 上傳資料夾總量上限
UPLOAD_CHUNK = 256 * 1024

# 圖片的檔頭魔術位元組：只看副檔名擋不住把任意檔案改名成 .png
IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF")

# 這些副檔名交給瀏覽器 inline 顯示會變成「以本服務的身分執行的網頁」，一律當附件下載
ACTIVE_CONTENT_EXTS = {".html", ".htm", ".svg", ".xhtml", ".xml", ".mhtml", ".js", ".mjs"}
INLINE_OK_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mov", ".webm",
                  ".m4v", ".mp3", ".wav", ".m4a", ".pdf", ".txt", ".md", ".csv", ".json"}


def _build_allowed_roots():
    """預設只開放本程式自己的上傳目錄。家目錄要明確在 config 打開才給。"""
    roots = [str(UPLOAD_DIR.resolve()).casefold().rstrip("\\") + "\\"]
    if CONFIG["allow_home_reads"]:
        roots.append(str(HOME).casefold().rstrip("\\") + "\\")
    for p in CONFIG["extra_file_roots"]:
        try:
            roots.append(str(Path(p).resolve()).casefold().rstrip("\\") + "\\")
        except Exception:
            log.warning("extra_file_roots 裡有無效路徑，略過：%s", p)
    return tuple(dict.fromkeys(roots))


ALLOWED_FILE_ROOTS = _build_allowed_roots()
