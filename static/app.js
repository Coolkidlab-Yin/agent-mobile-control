/* claude-chat 前端：聊天室 = session */
"use strict";

const $ = (s) => document.querySelector(s);
const listEl = $("#room-list");
const msgsEl = $("#messages");
const inputEl = $("#input");
const chatScreen = $("#screen-chat");

let rooms = [];
let current = null;          // {slug, sid, title, project, project_name}
let activeRun = null;        // {id, es}
let stickBottom = true;
let toolChips = {};          // tool_use_id -> chip element
let typingEl = null;
let roomsTimer = null;
let serverInfo = {};         // /api/health 的結果（桌面 app 有沒有裝、同步方式）
api("/api/health").then((h) => { serverInfo = h; }).catch(() => {});

const mode = () => localStorage.getItem("cc-mode") || "auto";
const modelPick = () => localStorage.getItem("cc-model") || "default";
const effortPick = () => localStorage.getItem("cc-effort") || "default";
const showAll = () => localStorage.getItem("cc-show-all") === "1";
let projFilter = localStorage.getItem("cc-proj") || "全部";

/* ---------- 深淺色主題 ---------- */
const themePick = () => localStorage.getItem("cc-theme") || "auto";
const sysDark = window.matchMedia("(prefers-color-scheme: dark)");

function applyTheme() {
  const t = themePick();
  const dark = t === "dark" || (t === "auto" && sysDark.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = dark ? "#0b0e17" : "#efebe2";
}
sysDark.addEventListener("change", () => { if (themePick() === "auto") applyTheme(); });
applyTheme();

/* 分段選鈕小工具 */
function bindSeg(id, getter, setter) {
  const box = $(id);
  const sync = () => {
    box.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === getter()));
  };
  box.querySelectorAll("button").forEach((b) => {
    b.onclick = () => { setter(b.dataset.v); sync(); };
  });
  sync();
  return sync;
}

/* ---------- 每個聊天室的模型/力度 ---------- */
const roomOv = (kind) => {
  if (!current) return null;
  if (current.sid) return localStorage.getItem("cc-room-" + kind + ":" + current.sid);
  return (current._ov || {})[kind] || null;
};
const setRoomOv = (kind, v) => {
  if (!current) return;
  if (current.sid) {
    const k = "cc-room-" + kind + ":" + current.sid;
    if (v === "default") localStorage.removeItem(k);
    else localStorage.setItem(k, v);
  } else {
    current._ov = current._ov || {};
    if (v === "default") delete current._ov[kind];
    else current._ov[kind] = v;
  }
};
const effModel = () => roomOv("model") || modelPick();
const effEffort = () => roomOv("effort") || effortPick();

// 模型清單（唯一來源；server.py 的 MODELS 要對得上 key）。short 給右上角按鈕，name/desc 給設定頁。
const MODEL_LIST = [
  { key: "default",  short: "預設",       name: "預設",       desc: "跟著電腦上目前的設定走，不特別指定。" },
  { key: "fable",    short: "Fable 5.1",  name: "Fable 5.1",  desc: "目前最聰明也最貴，留給真的很難的任務。" },
  { key: "fable5",   short: "Fable 5",    name: "Fable 5",    desc: "上一版 Fable，一樣貴，5.1 出來後通常沒必要選它。" },
  { key: "opus55",   short: "Opus 5.5",   name: "Opus 5.5",   desc: "最新的 Opus，比 Opus 5 便宜；預設想得比較少，要它認真就把思考力度調高。" },
  { key: "opus",     short: "Opus 5",     name: "Opus 5",     desc: "聰明的主力，日常大部分工作用它。" },
  { key: "opus48",   short: "Opus 4.8",   name: "Opus 4.8",   desc: "舊一代 Opus，想比對新舊表現時用。" },
  { key: "opus47",   short: "Opus 4.7",   name: "Opus 4.7",   desc: "舊一代 Opus。" },
  { key: "opus46",   short: "Opus 4.6",   name: "Opus 4.6",   desc: "舊一代 Opus。" },
  { key: "sonnet",   short: "Sonnet 5",   name: "Sonnet 5",   desc: "速度快、夠聰明，一般問答和小修改。" },
  { key: "sonnet46", short: "Sonnet 4.6", name: "Sonnet 4.6", desc: "舊一代 Sonnet。" },
  { key: "haiku",    short: "Haiku 4.5",  name: "Haiku 4.5",  desc: "最快最省，查小事、簡單問題用。" },
];
const MODEL_SHORT = Object.fromEntries(MODEL_LIST.map((m) => [m.key, m.short]));
(function renderModelOptions() {
  $("#seg-room-model").innerHTML = MODEL_LIST.map((m) =>
    '<button data-v="' + m.key + '">' + m.short + "</button>").join("");
  $("#model-opts").innerHTML = MODEL_LIST.map((m) =>
    '<label class="mode-opt"><input type="radio" name="model" value="' + m.key + '">' +
    "<div><b>" + m.name + "</b><span>" + m.desc + "</span></div></label>").join("");
})();
const EFFORT_SHORT = { default: "", max: "最深", high: "多想", medium: "中", low: "快" };

function updateRoomBtn() {
  const m = effModel(), e = effEffort();
  let label = MODEL_SHORT[m] || m;
  if (e !== "default") label += "·" + (EFFORT_SHORT[e] || e);
  $("#btn-room").textContent = label;
}

/* ---------- 小工具 ---------- */
function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d)) return "";
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const hm = pad(d.getHours()) + ":" + pad(d.getMinutes());
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return hm;
  const yd = new Date(now); yd.setDate(now.getDate() - 1);
  if (d.toDateString() === yd.toDateString()) return "昨天";
  if (d.getFullYear() === now.getFullYear()) return (d.getMonth() + 1) + "/" + d.getDate();
  return d.getFullYear() + "/" + (d.getMonth() + 1) + "/" + d.getDate();
}

function hueFor(name) {
  let h = 0;
  for (const ch of String(name)) h = (h * 31 + ch.codePointAt(0)) % 360;
  return h;
}

/* 極簡 markdown（assistant 泡泡用） */
function md(src) {
  const lines = String(src).split("\n");
  let html = "", inCode = false, codeBuf = [], listType = null, para = [];

  const flushPara = () => {
    if (para.length) { html += "<p>" + para.join("<br>") + "</p>"; para = []; }
  };
  const flushList = () => { if (listType) { html += "</" + listType + ">"; listType = null; } };
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

  for (const raw of lines) {
    if (raw.trimStart().startsWith("```")) {
      if (inCode) { html += "<pre><code>" + esc(codeBuf.join("\n")) + "</code></pre>"; codeBuf = []; }
      else { flushPara(); flushList(); }
      inCode = !inCode;
      continue;
    }
    if (inCode) { codeBuf.push(raw); continue; }
    const line = raw;
    const t = line.trim();
    if (!t) { flushPara(); flushList(); continue; }
    let m;
    if ((m = t.match(/^(#{1,4})\s+(.*)/))) {
      flushPara(); flushList();
      html += "<h3>" + inline(m[2]) + "</h3>";
    } else if ((m = t.match(/^[-*]\s+(.*)/))) {
      flushPara();
      if (listType !== "ul") { flushList(); html += "<ul>"; listType = "ul"; }
      html += "<li>" + inline(m[1]) + "</li>";
    } else if ((m = t.match(/^\d+[.)]\s+(.*)/))) {
      flushPara();
      if (listType !== "ol") { flushList(); html += "<ol>"; listType = "ol"; }
      html += "<li>" + inline(m[1]) + "</li>";
    } else if (t.startsWith(">")) {
      flushPara(); flushList();
      html += "<blockquote>" + inline(t.replace(/^>\s?/, "")) + "</blockquote>";
    } else if (t.startsWith("|") || t.startsWith("---")) {
      flushPara(); flushList();
      html += "<pre><code>" + esc(line) + "</code></pre>";
    } else {
      flushList(); para.push(inline(line));
    }
  }
  if (inCode && codeBuf.length) html += "<pre><code>" + esc(codeBuf.join("\n")) + "</code></pre>";
  flushPara(); flushList();
  // 合併相鄰的表格 pre
  return html.replace(/<\/code><\/pre><pre><code>/g, "\n");
}

/* 把回覆裡的 Windows 檔案路徑變成可預覽的影片/圖片/連結 */
const FILE_RE = /[A-Za-z]:(?:\\|\/)[^\s"'<>|?*`]+?\.(mp4|mov|webm|m4v|png|jpg|jpeg|gif|webp|mp3|wav|m4a|pdf|html|htm|md|txt|csv|json)\b/gi;

function fileEmbed(match, ext) {
  const raw = match.replace(/&amp;/g, "&");
  const url = "/api/file?path=" + encodeURIComponent(raw);
  const base = raw.split(/[\\/]/).pop();
  const e = ext.toLowerCase();
  if (["mp4", "mov", "webm", "m4v"].includes(e)) {
    return '<video controls playsinline preload="metadata" src="' + url + '"></video>' +
      '<a class="file-link" href="' + url + '" target="_blank">🎬 ' + esc(base) + "</a>";
  }
  if (["png", "jpg", "jpeg", "gif", "webp"].includes(e)) {
    return '<a href="' + url + '" target="_blank"><img src="' + url + '" loading="lazy"></a>';
  }
  if (["mp3", "wav", "m4a"].includes(e)) {
    return '<audio controls preload="none" src="' + url + '"></audio>' +
      '<a class="file-link" href="' + url + '" target="_blank">🎵 ' + esc(base) + "</a>";
  }
  return '<a class="file-link" href="' + url + '" target="_blank">📎 ' + esc(base) + "</a>";
}

function linkifyFiles(html) {
  // <pre> 區塊裡只給連結不嵌播放器，其他地方給完整預覽
  return html.split(/(<pre>[\s\S]*?<\/pre>)/).map((seg) => {
    if (seg.startsWith("<pre>")) {
      return seg.replace(FILE_RE, (m) => {
        const raw = m.replace(/&amp;/g, "&");
        return '<a href="/api/file?path=' + encodeURIComponent(raw) + '" target="_blank">' + m + "</a>";
      });
    }
    return seg.replace(FILE_RE, fileEmbed);
  }).join("");
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = "HTTP " + r.status;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* ignore */ }
    throw new Error(msg);
  }
  return r.json();
}

/* ---------- 聊天室清單 ---------- */
/* 桌面 session 在等授權的卡（hook 丟過來的），清單頂端提示 */
let pendingPerms = [];
function renderPermBanner() {
  let bar = $("#perm-banner");
  if (!pendingPerms.length) { if (bar) bar.remove(); return; }
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "perm-banner";
    bar.className = "perm-banner";
    listEl.parentNode.insertBefore(bar, listEl);
  }
  const p = pendingPerms[0];
  const room = rooms.find((r) => (r.gen_sids || [r.sid]).includes(p.sid));
  const isAsk = p.tool === "AskUserQuestion";
  bar.innerHTML = (isAsk ? "🙋 " : "⚠️ ") + (pendingPerms.length > 1 ? pendingPerms.length + " 個對話" : "「" + esc(room ? room.title : "某個對話") + "」") +
    (isAsk ? "在等你回答問題" : "在等你授權危險指令") + " <span>點這裡去按</span>";
  bar.onclick = () => { if (room) openRoom(room); };
}

async function loadRooms(silent) {
  try {
    const data = await api("/api/rooms" + (showAll() ? "?all=1" : ""));
    rooms = data.rooms;
    pendingPerms = data.pending_perms || [];
    renderChips();
    renderRooms();
    renderPermBanner();
  } catch (e) {
    if (!silent) listEl.innerHTML = '<div class="empty-hint">連不上伺服器：' + esc(e.message) + "</div>";
  }
}

function renderChips() {
  const names = [];
  for (const r of rooms) {
    if (r.project_name && !names.includes(r.project_name)) names.push(r.project_name);
  }
  if (projFilter !== "全部" && !names.includes(projFilter)) projFilter = "全部";
  const box = $("#proj-chips");
  box.innerHTML = "";
  for (const name of ["全部", ...names]) {
    const el = document.createElement("div");
    el.className = "chip" + (name === projFilter ? " on" : "");
    el.textContent = name;
    el.onclick = () => {
      projFilter = name;
      localStorage.setItem("cc-proj", name);
      renderChips();
      renderRooms();
    };
    box.appendChild(el);
  }
}

/* 全文搜尋（對話內容命中）：輸入 ≥2 字後 400ms 問一次伺服器 */
let searchHits = { q: "", map: {} };
let searchTimer = null;
function scheduleSearch(q) {
  clearTimeout(searchTimer);
  if (q.length < 2) { searchHits = { q: "", map: {} }; return; }
  searchTimer = setTimeout(async () => {
    try {
      const r = await api("/api/search?q=" + encodeURIComponent(q) + (showAll() ? "&all=1" : ""));
      const map = {};
      for (const room of r.rooms) map[room.sid] = room;
      searchHits = { q, map };
      if ($("#search").value.trim().toLowerCase() === q) renderRooms();
    } catch (e) { /* 搜不到就只留標題過濾 */ }
  }, 400);
}

function renderRooms() {
  const q = $("#search").value.trim().toLowerCase();
  let shown = projFilter === "全部" ? rooms : rooms.filter((r) => r.project_name === projFilter);
  if (q) {
    if (searchHits.q !== q) scheduleSearch(q);
    const hits = searchHits.q === q ? searchHits.map : {};
    shown = shown.filter((r) => hits[r.sid] || (r.title + r.preview + r.project_name).toLowerCase().includes(q));
    shown = shown.map((r) => {
      const h = hits[r.sid];
      if (!h || !h.hits.length) return r;
      const first = h.hits[0];
      return Object.assign({}, r, { preview: "🔎 " + (first.role === "user" ? "你：" : "") + first.snippet });
    });
  }
  if (!shown.length) {
    listEl.innerHTML = '<div class="empty-hint">' + (q ? (q.length >= 2 && searchHits.q !== q ? "搜尋對話內容中…" : "沒有符合的聊天室") : "這個專案還沒有聊天室") + "</div>";
    return;
  }
  listEl.innerHTML = "";
  for (const r of shown) {
    const el = document.createElement("div");
    el.className = "room";
    const h = hueFor(r.project_name);
    const initial = (r.project_name || "?").slice(0, 1).toUpperCase();
    const eng = r.engine || "claude";
    el.innerHTML =
      '<div class="avatar' + (eng !== "claude" ? " eng-" + eng : "") + '" style="--h:' + h + '">' +
        esc(eng === "claude" ? initial : (ENGINE_ICON[eng] || "?")) +
        (r.running || r.busy ? '<span class="dot"></span>' : "") +
      "</div>" +
      '<div class="room-main">' +
        '<div class="room-top"><div class="room-title">' + esc(r.title) + '</div>' +
        '<div class="room-time">' + fmtTime(r.ts) + "</div></div>" +
        '<div class="room-bottom">' +
          '<div class="room-preview">' + esc(r.running ? "正在工作中…" : r.busy ? "桌面工作中… " + (r.preview || "") : r.preview || "") + "</div>" +
          (eng !== "claude" ? '<span class="tag eng">' + esc(ENGINE_NAME[eng] || eng) + "</span>" : "") +
          '<span class="tag">' + esc(r.project_name) + "</span>" +
          (r.archived ? '<span class="tag arch">封存</span>' : "") +
          (r.live ? '<span class="tag live">桌機開著</span>' : "") +
          (eng === "claude" && r.app && !r.desktop ? '<span class="tag phone">只在手機</span>' : "") +
          (r.perm ? '<span class="tag perm">⚠ 等你授權</span>' : "") +
          (r.ask ? '<span class="tag perm">🙋 等你回答</span>' : "") +
        "</div>" +
      "</div>";
    const wrap = document.createElement("div");
    wrap.className = "room-wrap";
    const acts = document.createElement("div");
    acts.className = "room-actions";
    acts.innerHTML =
      '<button class="ra-arch">' + (r.archived ? "解封" : "封存") + "</button>" +
      '<button class="ra-del">刪除</button>';
    acts.querySelector(".ra-arch").onclick = () => doArchive(r);
    acts.querySelector(".ra-del").onclick = () => doDelete(r);
    wrap.appendChild(acts);
    wrap.appendChild(el);
    attachRoomHandlers(el, r);
    listEl.appendChild(wrap);
  }
}

/* ---------- 左滑 / 長按聊天室 → 封存/刪除 ---------- */
let actionRoom = null;
let openRow = null;
const SWIPE_W = 136;

function closeOpenRow() {
  if (openRow && openRow._close) openRow._close();
  openRow = null;
}
listEl.addEventListener("scroll", closeOpenRow, { passive: true });

async function doArchive(r) {
  $("#sheet-actions").classList.add("hidden");
  closeOpenRow();
  try {
    await api("/api/room/" + r.slug + "/" + r.sid + "/archive", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ on: !r.archived }),
    });
    loadRooms(true);
  } catch (e) { alert("封存失敗：" + e.message); }
}

async function doDelete(r) {
  const ok = confirm("確定刪除「" + r.title + "」？\n\n對話紀錄會移到電腦上的垃圾桶資料夾（可救回），桌面 app 也會看不到這個對話。");
  $("#sheet-actions").classList.add("hidden");
  closeOpenRow();
  if (!ok) return;
  try {
    await api("/api/room/" + r.slug + "/" + r.sid + "/delete", { method: "POST" });
    loadRooms(true);
  } catch (e) { alert("刪除失敗：" + e.message); }
}

function attachRoomHandlers(el, r) {
  let sx = 0, sy = 0, dx = 0, base = 0;
  let dragging = false, fired = false, timer = null;
  const setX = (x, anim) => {
    el.style.transition = anim ? "" : "none";
    el.style.transform = "translateX(" + x + "px)";
  };
  const open = () => { closeOpenRow(); setX(-SWIPE_W, true); el.dataset.open = "1"; openRow = el; };
  const close = () => { setX(0, true); delete el.dataset.open; if (openRow === el) openRow = null; };
  el._close = close;

  el.addEventListener("pointerdown", (e) => {
    sx = e.clientX; sy = e.clientY; dx = 0;
    dragging = false; fired = false;
    base = el.dataset.open ? -SWIPE_W : 0;
    timer = setTimeout(() => { if (!dragging) { fired = true; openActions(r); } }, 550);
  });
  el.addEventListener("pointermove", (e) => {
    const mx = e.clientX - sx, my = e.clientY - sy;
    if (!dragging) {
      if (Math.abs(mx) > 10 && Math.abs(mx) > Math.abs(my)) {
        dragging = true;
        clearTimeout(timer);
        try { el.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      } else if (Math.abs(my) > 10) {
        clearTimeout(timer);
      }
    }
    if (dragging) {
      dx = mx;
      setX(Math.min(0, Math.max(-SWIPE_W - 20, base + mx)), false);
    }
  });
  const end = () => {
    clearTimeout(timer);
    if (dragging) {
      if (base + dx < -SWIPE_W / 2) open();
      else close();
    }
  };
  el.addEventListener("pointerup", end);
  el.addEventListener("pointercancel", end);
  el.addEventListener("contextmenu", (e) => { e.preventDefault(); openActions(r); });
  el.onclick = () => {
    if (fired || dragging) { fired = false; dragging = false; return; }
    if (openRow && openRow !== el) { closeOpenRow(); return; }
    if (el.dataset.open) { close(); return; }
    openRoom(r);
  };
}

function openActions(r) {
  actionRoom = r;
  $("#action-title").textContent = r.title;
  $("#btn-archive").textContent = r.archived ? "📤 解除封存" : "📥 封存（從清單收起來，紀錄還在）";
  const isClaude = (r.engine || "claude") === "claude";
  $("#btn-desktop").classList.toggle("hidden", !isClaude || !serverInfo.desktop_app || serverInfo.desktop_sync === "off");
  $("#btn-desktop").textContent = r.desktop ? "🖥 在桌面 App 打開" : "🖥 同步到桌面 App 並打開";
  $("#sheet-actions").classList.remove("hidden");
}

$("#btn-archive").onclick = () => { if (actionRoom) doArchive(actionRoom); };
$("#btn-delete").onclick = () => { if (actionRoom) doDelete(actionRoom); };
$("#btn-desktop").onclick = async () => {
  const r = actionRoom;
  if (!r) return;
  $("#sheet-actions").classList.add("hidden");
  try {
    const d = await api("/api/room/" + r.slug + "/" + r.sid + "/desktop", { method: "POST" });
    sysNote(d.result === "imported" ? "已登錄進桌面 App，電腦那邊已切到這個對話" : "桌面 App 已切到這個對話");
    setTimeout(() => loadRooms(true), 3000);
  } catch (e) {
    sysNote("同步失敗：" + e.message, true);
  }
};
$("#btn-rename").onclick = async () => {
  const r = actionRoom;
  if (!r) return;
  const t = prompt("聊天室名字（清空 = 還原成原本的）", r.title || "");
  if (t === null) return;
  $("#sheet-actions").classList.add("hidden");
  try {
    await api("/api/room/" + r.slug + "/" + r.sid + "/title", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: t.trim() }),
    });
    if (current && current.sid === r.sid) $("#chat-title").textContent = t.trim() || r.title;
    loadRooms(true);
  } catch (e) {
    sysNote("改名失敗：" + e.message, true);
  }
};

/* ---------- 進出聊天室 ---------- */
/* 上下文用量條 */
function setCtx(c) {
  const bar = $("#ctx-bar");
  if (!c || !c.tokens) { bar.classList.add("hidden"); return; }
  bar.classList.remove("hidden");
  const fill = $("#ctx-fill");
  fill.style.width = Math.min(100, c.pct) + "%";
  fill.className = c.pct >= 85 ? "hot" : c.pct >= 70 ? "warn" : "";
  $("#ctx-label").textContent = "上下文 " + fmtTok(c.tokens) + " / " + fmtTok(c.window) + " · " + c.pct + "%";
}

/* ---------- 旁觀桌面正在跑的對話（尾讀 jsonl） ---------- */
let watchTimer = null;
let watchOffset = 0;
let watchBusy = false;

function applyTailItems(items) {
  for (const it of items) {
    if (it.kind === "ctx") { setCtx(it); continue; }
    if (it.kind === "tool_ok") {
      const chip = toolChips[it.tool_use_id];
      if (chip) {
        const st = chip.querySelector(".t-state");
        if (st) { st.className = "t-state " + (it.ok ? "ok" : "bad"); st.textContent = it.ok ? "✓" : "✕"; }
      }
      continue;
    }
    hideTyping();
    it.ts = it.ts || Date.now();
    msgsEl.appendChild(renderItem(it));
    scrollBottom();
  }
}

function stopWatch() {
  if (watchTimer) { clearInterval(watchTimer); watchTimer = null; }
  if (watchBusy) { watchBusy = false; hideTyping(); if (current) setSub(""); }
}

function startWatch() {
  stopWatch();
  if (!current || !current.sid || !current.slug || activeRun) return;
  if ((current.engine || "claude") !== "claude") return;
  const sid = current.sid;
  const tick = async () => {
    if (!current || current.sid !== sid || activeRun || document.hidden) return;
    let d;
    try {
      d = await api("/api/tail/" + current.slug + "/" + sid + "?offset=" + watchOffset);
    } catch (e) { return; }
    if (!current || current.sid !== sid || activeRun) return;
    if (d.running) {
      // 手機這邊起的工作（例如背景重連）→ 交給事件流
      const st = await api("/api/status").catch(() => null);
      const info = st && st.running[sid];
      if (info) { attachRun(info.run_id, info.n_events, true, info.peer); return; }
    }
    watchOffset = d.offset;
    if (d.items && d.items.length) applyTailItems(d.items);
    for (const p of d.pending || []) {
      if (!msgsEl.querySelector('.perm-card[data-perm-id="' + p.perm_id + '"]')) { hideTyping(); renderPermCard(p); scrollBottom(); }
    }
    for (const p of d.answered || []) lockPermCard(p.perm_id, permNote(p.answer, p.by, p.tool));
    if (d.busy !== watchBusy) {
      watchBusy = d.busy;
      if (d.busy) { setSub("桌面工作中…"); showTyping(); scrollBottom(); }
      else { hideTyping(); setSub(""); }
    } else if (d.busy && d.items && d.items.length) {
      showTyping(); scrollBottom();
    }
  };
  watchTimer = setInterval(tick, 2500);
  tick();
}

function openRoom(room, fromPop) {
  current = Object.assign({}, room);
  toolChips = {};
  $("#chat-title").textContent = room.title || "新聊天室";
  setSub("");
  setCtx(null);
  updateRoomBtn();
  // 模型/力度按鈕只對 Claude Code 有意義
  $("#btn-room").classList.toggle("hidden", (room.engine || "claude") !== "claude");
  msgsEl.innerHTML = "";
  chatScreen.classList.remove("hidden-right");
  if (!fromPop) history.pushState({ chat: 1 }, "");
  stickBottom = true;
  if (room.sid) {
    loadHistory().then((data) => {
      // 若這個房間有背景工作進行中 → 接上事件流；否則旁觀桌面那邊的進度
      // （run 資訊由 history 一起帶回，省掉一趟 /api/status——手機常在慢連線上）
      const info = data && data.run;
      if (info) attachRun(info.run_id, info.n_events, true, info.peer);
      else startWatch();
    }).catch(() => startWatch());
  } else {
    msgsEl.innerHTML = '<div class="sys-note">新聊天室（' + esc(room.project_name) + '）— 送出第一句就開始</div>';
  }
}

function closeRoom() {
  hideViewers();
  chatScreen.classList.add("hidden-right");
  stopWatch();
  detachRun(false);
  current = null;
  loadRooms(true);
}

window.addEventListener("popstate", () => {
  // 看圖／看檔開著時，任何返回（左上鍵、系統邊緣手勢、安卓返回鍵）先關它，不要把底下的房間關掉
  if (viewerOpen()) { hideViewers(); return; }
  if (current) closeRoom();
});
function goBack() {
  if (history.state && history.state.chat) history.back();
  else closeRoom();
}
$("#btn-back").onclick = goBack;

const ENGINE_ICON = { claude: "✳", codex: "◆", grok: "𝕏", gemini: "✦", openai: "◎", deepseek: "◇", openrouter: "⇄" };
const ENGINE_NAME = { claude: "Claude Code", codex: "Codex", grok: "Grok", gemini: "Gemini", openai: "ChatGPT", deepseek: "DeepSeek", openrouter: "OpenRouter" };

function setSub(state) {
  const parts = [];
  if (current && current.engine && current.engine !== "claude") {
    parts.push(esc(ENGINE_NAME[current.engine] || current.engine));
  }
  if (current && current.project_name) parts.push(esc(current.project_name));
  const el = $("#chat-sub");
  el.innerHTML = parts.join(" · ") + (state ? ' · <span class="working">' + state + "</span>" : "");
}

/* ---------- 歷史訊息 ---------- */
async function loadHistory(before) {
  if (!current || !current.sid) return;
  const url = "/api/history/" + current.slug + "/" + current.sid +
    (before != null ? "?before=" + before : "");
  const data = await api(url);
  if (before == null && data.context) setCtx(data.context);
  if (before == null && typeof data.size === "number") watchOffset = data.size;
  const frag = document.createDocumentFragment();
  if (data.more) {
    const btn = document.createElement("div");
    btn.className = "load-more";
    btn.textContent = "載入更早的訊息";
    btn.onclick = () => { btn.remove(); loadHistory(data.oldest); };
    frag.appendChild(btn);
  }
  for (const it of data.items) frag.appendChild(renderItem(it));
  if (before != null) {
    const oldH = msgsEl.scrollHeight;
    msgsEl.prepend(frag);
    msgsEl.scrollTop += msgsEl.scrollHeight - oldH;
  } else {
    msgsEl.innerHTML = "";
    msgsEl.appendChild(frag);
    scrollBottom(true);
  }
  return data;
}

function renderItem(it) {
  if (it.kind === "info") {
    const el = document.createElement("div");
    el.className = "info-chip";
    el.innerHTML = "📋 " + esc(it.label || "摘要") +
      '（點開看）<div class="full">' + esc(it.text) + "</div>";
    el.onclick = () => el.classList.toggle("expand");
    return el;
  }
  if (it.kind === "tool") {
    const el = document.createElement("div");
    el.className = "tool-chip";
    const state = it.ok === true ? '<span class="t-state ok">✓</span>'
      : it.ok === false ? '<span class="t-state bad">✕</span>'
      : '<span class="t-state"><span class="spin"></span></span>';
    el.innerHTML = '<span class="t-name">⚙ ' + esc(it.tool) + "</span>" +
      (it.detail ? '<span class="t-detail">' + esc(it.detail) + "</span>" : "") + state;
    el.onclick = () => el.classList.toggle("expand");
    if (it.tool_use_id) toolChips[it.tool_use_id] = el;
    return el;
  }
  const el = document.createElement("div");
  el.className = "msg " + (it.role === "user" ? "user" : "ai");
  const body = it.role === "user"
    ? '<div class="bubble">' + linkifyFiles(esc(it.text).replace(/\n/g, "<br>")) + "</div>"
    : '<div class="bubble">' + linkifyFiles(md(it.text)) + "</div>";
  el.innerHTML = body + (it.ts ? '<div class="msg-time">' + fmtTime(it.ts) + "</div>" : "");
  return el;
}

/* ---------- 捲動 ---------- */
function scrollBottom(force) {
  if (force || stickBottom) msgsEl.scrollTop = msgsEl.scrollHeight;
}
msgsEl.addEventListener("scroll", () => {
  stickBottom = msgsEl.scrollHeight - msgsEl.scrollTop - msgsEl.clientHeight < 80;
  $("#jump-bottom").classList.toggle("hidden", stickBottom);
});
$("#jump-bottom").onclick = () => { stickBottom = true; scrollBottom(true); $("#jump-bottom").classList.add("hidden"); };

/* ---------- 打字中指示 ---------- */
function showTyping() {
  if (typingEl) return;
  typingEl = document.createElement("div");
  typingEl.className = "msg ai";
  typingEl.innerHTML = '<div class="bubble typing"><i></i><i></i><i></i></div>';
  msgsEl.appendChild(typingEl);
  scrollBottom();
}
function hideTyping() { if (typingEl) { typingEl.remove(); typingEl = null; } }

/* ---------- 附件上傳 ---------- */
let attachments = [];   // {path, name, url, uploading}

const isImagePath = (p) => /\.(png|jpe?g|gif|webp)$/i.test(p || "");

function renderAttachStrip() {
  const strip = $("#attach-strip");
  if (!attachments.length) { strip.classList.add("hidden"); strip.innerHTML = ""; return; }
  strip.classList.remove("hidden");
  strip.innerHTML = "";
  attachments.forEach((a, i) => {
    const el = document.createElement("div");
    el.className = "attach-item" + (a.uploading ? " uploading" : "");
    el.innerHTML = (isImagePath(a.name) ? '<img src="' + a.url + '">'
      : '<div class="doc">📄<span>' + esc((a.name || "").split(".").pop().toUpperCase()) + "</span></div>") +
      '<div class="rm">✕</div>';
    el.querySelector(".rm").onclick = () => { attachments.splice(i, 1); renderAttachStrip(); };
    strip.appendChild(el);
  });
}

$("#btn-attach").onclick = () => $("#file-input").click();
$("#file-input").addEventListener("change", async (e) => {
  for (const f of e.target.files) {
    const item = { path: null, name: f.name, url: URL.createObjectURL(f), uploading: true };
    attachments.push(item);
    renderAttachStrip();
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await fetch("/api/upload", { method: "POST", body: fd });
      if (!r.ok) throw new Error((await r.json()).detail || "HTTP " + r.status);
      const d = await r.json();
      item.path = d.path;
      item.uploading = false;
    } catch (err) {
      attachments = attachments.filter((x) => x !== item);
      sysNote("上傳失敗：" + err.message, true);
    }
    renderAttachStrip();
  }
  e.target.value = "";
});

/* ---------- 送訊息與事件流 ---------- */
async function sendMsg() {
  let text = inputEl.value.trim();
  if ((!text && !attachments.length) || !current) return;
  if (activeRun && !activeRun.peer) return;
  const interrupting = !!(activeRun && activeRun.peer);
  if (attachments.some((a) => a.uploading)) { sysNote("圖片還在上傳，等一下再送"); return; }
  const paths = attachments.map((a) => a.path).filter(Boolean);
  if (paths.length) {
    const lines = paths.map((p) => (isImagePath(p) ? "[手機傳圖，請用 Read 工具查看: " : "[手機傳檔，請用 Read 工具讀取: ") + p + "]");
    text = (text ? text + "\n\n" : "") + lines.join("\n");
  }
  attachments = [];
  renderAttachStrip();
  inputEl.value = "";
  autoGrow();
  const el = document.createElement("div");
  el.className = "msg user";
  el.innerHTML = '<div class="bubble">' + linkifyFiles(esc(text).replace(/\n/g, "<br>")) + "</div>" +
    '<div class="msg-time">' + fmtTime(Date.now()) + "</div>";
  msgsEl.appendChild(el);
  stickBottom = true;
  scrollBottom(true);
  showTyping();
  try {
    const body = { text, mode: mode(), engine: current.engine || "claude" };
    if ((current.engine || "claude") === "claude") {
      if (effModel() !== "default") body.model = effModel();
      if (effEffort() !== "default") body.effort = effEffort();
    }
    if (current.sid) { body.sid = current.sid; body.slug = current.slug; }
    else body.project = current.project;
    const r = await api("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (interrupting && activeRun && r.run_id === activeRun.id) return; // 插話：同一條事件流繼續看
    attachRun(r.run_id, 0, false, r.peer);
  } catch (e) {
    hideTyping();
    sysNote("送不出去：" + e.message, true);
  }
}

function attachRun(runId, from, isReattach, peer) {
  stopWatch();
  detachRun(true);
  const es = new EventSource("/api/run/" + runId + "/events?start=" + (from || 0));
  activeRun = { id: runId, es, peer: !!peer };
  setSub(peer ? "桌面工作中…（可直接打字插話）" : "Claude 工作中…");
  // 直送桌面的工作：停止＝打斷桌面的動作；輸入框保持可用，送出＝打斷並插話
  $("#btn-stop").textContent = peer ? "打斷" : "■";
  $("#btn-send").classList.toggle("hidden", !peer);
  $("#btn-stop").classList.remove("hidden");
  if (isReattach) showTyping();

  es.onmessage = (ev) => {
    let it;
    try { it = JSON.parse(ev.data); } catch (e) { return; }
    if (it.kind === "init") {
      if (current && !current.sid && it.sid) current.sid = it.sid;
      return;
    }
    if (it.kind === "ctx") {
      setCtx(it);
      return;
    }
    if (it.kind === "note") {
      sysNote(it.text);
      return;
    }
    if (it.kind === "ask") {
      hideTyping();
      renderAskCard(it);
      showTyping();
      scrollBottom();
      return;
    }
    if (it.kind === "ask_done") {
      lockAskCard(it.ask_id, null);
      return;
    }
    if (it.kind === "perm") {
      hideTyping();
      renderPermCard(it);
      showTyping();
      scrollBottom();
      return;
    }
    if (it.kind === "perm_done") {
      lockPermCard(it.perm_id, permNote(it.decision, it.by, it.tool));
      return;
    }
    if (it.kind === "tool_ok") {
      const chip = toolChips[it.tool_use_id];
      if (chip) {
        const st = chip.querySelector(".t-state");
        if (st) { st.className = "t-state " + (it.ok ? "ok" : "bad"); st.textContent = it.ok ? "✓" : "✕"; }
      }
      return;
    }
    if (it.kind === "done") {
      finishRun(it);
      return;
    }
    hideTyping();
    it.ts = it.ts || Date.now();
    msgsEl.appendChild(renderItem(it));
    showTyping();
    scrollBottom();
  };
  es.onerror = () => {
    // 連線斷了：工作若還在跑，稍後重連；不在了就收尾
    if (!activeRun || activeRun.id !== runId) return;
    es.close();
    setTimeout(async () => {
      if (!activeRun || activeRun.id !== runId) return;
      try {
        const st = await api("/api/status");
        const still = current && current.sid && st.running[current.sid];
        if (still && still.run_id === runId) attachRun(runId, 0, true, still.peer);
        else finishRun({ ok: true });
      } catch (e) { finishRun({ ok: false, error: "連線中斷" }); }
    }, 1500);
  };
}

function detachRun(keepUI) {
  if (activeRun && activeRun.es) activeRun.es.close();
  activeRun = null;
  if (!keepUI) {
    hideTyping();
    $("#btn-send").classList.remove("hidden");
    $("#btn-stop").classList.add("hidden");
  }
}

async function finishRun(doneEv) {
  const wasNew = current && !current.slug;
  detachRun(false);
  setSub("");
  if (doneEv.error) sysNote("出了點問題：" + doneEv.error, true);
  if (!current) return;
  if (doneEv.sid && !current.sid) current.sid = doneEv.sid;
  // 用檔案裡的正式紀錄取代串流畫面（順便補時間戳）
  if (wasNew || !current.slug) {
    await loadRooms(true);
    const found = rooms.find((r) => r.sid === current.sid);
    if (found) { current.slug = found.slug; $("#chat-title").textContent = found.title; }
  }
  if (current.slug && current.sid) {
    setTimeout(() => {
      if (current && !activeRun) loadHistory().then(() => startWatch()).catch(() => {});
    }, 400);
  }
  loadRooms(true);
}

/* ---------- AskUserQuestion 選項卡 ---------- */
function renderAskCard(it) {
  const card = buildAskCard(it.questions, "🙋 它想問你", (answers, freeText, skipped) => {
    api("/api/ask/" + it.ask_id + "/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers: answers || {}, free_text: freeText || "", skipped: !!skipped }),
    }).catch(() => {});
    lockAskCard(it.ask_id, skipped ? "（已跳過）" : null);
  });
  card.dataset.askId = it.ask_id;
  msgsEl.appendChild(card);
}

/* 選項卡本體：questions 依 AskUserQuestion 格式；submit(answers, freeText, skipped) */
function buildAskCard(questions, head, submit) {
  const card = document.createElement("div");
  card.className = "msg ai ask-card";
  const picked = {};   // question -> Set(labels)

  let html = '<div class="ask-head">' + head + "</div>";
  for (const q of questions || []) {
    const multi = !!q.multiSelect;
    html += '<div class="ask-q" data-q="' + esc(q.question) + '" data-multi="' + (multi ? 1 : 0) + '">';
    if (q.header) html += '<div class="ask-tag">' + esc(q.header) + "</div>";
    html += '<div class="ask-question">' + esc(q.question) + "</div>";
    for (const o of q.options || []) {
      html += '<button class="ask-opt" data-label="' + esc(o.label) + '">' +
        '<b>' + esc(o.label) + "</b>" +
        (o.description ? "<span>" + esc(o.description) + "</span>" : "") +
        "</button>";
    }
    if (multi) html += '<button class="ask-multi-ok">就選這些</button>';
    html += "</div>";
  }
  html += '<div class="ask-free"><input type="text" placeholder="或用打字回答…">' +
    '<button class="ask-send">送出</button></div>' +
    '<button class="ask-skip">跳過，讓它自己決定</button>';
  card.innerHTML = html;

  card.querySelectorAll(".ask-q").forEach((qEl) => {
    const qText = qEl.dataset.q;
    const multi = qEl.dataset.multi === "1";
    qEl.querySelectorAll(".ask-opt").forEach((btn) => {
      btn.onclick = () => {
        const label = btn.dataset.label;
        if (multi) {
          const set = picked[qText] = picked[qText] || new Set();
          if (set.has(label)) { set.delete(label); btn.classList.remove("on"); }
          else { set.add(label); btn.classList.add("on"); }
          return;
        }
        picked[qText] = new Set([label]);
        btn.classList.add("on");
        // 單選題全部答完才送出
        const allQ = [...card.querySelectorAll(".ask-q")];
        if (allQ.every((x) => picked[x.dataset.q] && picked[x.dataset.q].size)) {
          const answers = {};
          for (const [k, v] of Object.entries(picked)) answers[k] = [...v].join("、");
          submit(answers, "", false);
        }
      };
    });
  });
  card.querySelectorAll(".ask-multi-ok").forEach((btn) => {
    btn.onclick = () => {
      const answers = {};
      for (const [k, v] of Object.entries(picked)) answers[k] = [...v].join("、");
      submit(answers, "", false);
    };
  });
  card.querySelector(".ask-send").onclick = () => {
    const v = card.querySelector(".ask-free input").value.trim();
    if (!v) return;
    const answers = {};
    for (const [k, s] of Object.entries(picked)) answers[k] = [...s].join("、");
    submit(answers, v, false);
  };
  card.querySelector(".ask-skip").onclick = () => submit({}, "", true);
  return card;
}

function lockAskCard(askId, note) {
  const card = msgsEl.querySelector('.ask-card[data-ask-id="' + askId + '"]');
  if (!card || card.classList.contains("done")) return;
  card.classList.add("done");
  card.querySelectorAll("button, input").forEach((el) => { el.disabled = true; });
  if (note) {
    const n = document.createElement("div");
    n.className = "ask-note";
    n.textContent = note;
    card.appendChild(n);
  }
}

/* ---------- 逐項授權卡（先問我模式／危險指令，桌面 session 的也會出現在這） ---------- */
function permNote(decision, by, tool) {
  if (tool === "AskUserQuestion") {
    if (by === "desktop") return "已在桌面回答了";
    if (by === "timeout" || decision === "ask") return "太久沒人答，改由桌面 App 自己問";
    return decision === "deny" ? "（已跳過）" : "已回答";
  }
  const who = by === "desktop" ? "已在桌面按了：" : by === "timeout" ? "沒人按，改由桌面 App 自己問：" : "";
  if (decision === "allow") return who + "已允許";
  if (decision === "deny") return who + "已拒絕";
  if (decision === "ask") return "太久沒人按，改由桌面 App 的確認框處理";
  return who || null;
}

function renderPermCard(it) {
  if (it.tool === "AskUserQuestion" && it.questions) {
    // 桌面正在跑的對話問選擇題：桌面 App 也跳了同一題，先答的算數；答案走授權通道回填
    const qc = buildAskCard(it.questions, "🙋 桌面的對話想問你（桌面也跳了同一題，先答的算數）", (answers, freeText, skipped) => {
      api("/api/perm/" + it.perm_id + "/answer", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision: skipped ? "deny" : "allow", answers: answers || {}, free_text: freeText || "" }),
      }).catch(() => {});
      lockPermCard(it.perm_id, skipped ? "（已跳過）" : "已回答");
    });
    qc.classList.add("perm-card");
    qc.dataset.permId = it.perm_id;
    msgsEl.appendChild(qc);
    return;
  }
  const card = document.createElement("div");
  card.className = "msg ai ask-card perm-card";
  card.dataset.permId = it.perm_id;
  const head = it.reason
    ? (it.source === "desktop" ? "⚠️ 桌面正在跑的對話碰到危險指令，要放行嗎？（桌面也跳了同一個框，先按的算數）" : "⚠️ 危險指令，要放行嗎？")
    : "🔐 它想做這件事，可以嗎？";
  card.innerHTML =
    '<div class="ask-head">' + head + "</div>" +
    (it.reason ? '<div class="perm-reason">' + esc(it.reason) + "</div>" : "") +
    '<div class="ask-tag">' + esc(it.tool || "工具") + "</div>" +
    (it.detail ? '<div class="ask-question">' + esc(it.detail) + "</div>" : "") +
    (it.preview && it.preview !== it.detail ? '<pre class="perm-preview">' + esc(it.preview) + "</pre>" : "") +
    '<div class="perm-btns">' +
      '<button class="ask-opt perm-allow"><b>允許</b></button>' +
      '<button class="ask-opt perm-deny"><b>拒絕</b></button>' +
    "</div>" +
    (it.source === "desktop" ? "" : '<button class="ask-skip perm-all">這次工作剩下的全部允許（不再問）</button>');
  const submit = (decision) => {
    api("/api/perm/" + it.perm_id + "/answer", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    }).catch(() => {});
    lockPermCard(it.perm_id, decision === "deny" ? "已拒絕" : decision === "allow_all" ? "已允許（之後不再問）" : "已允許");
  };
  card.querySelector(".perm-allow").onclick = () => submit("allow");
  card.querySelector(".perm-deny").onclick = () => submit("deny");
  const allBtn = card.querySelector(".perm-all");
  if (allBtn) allBtn.onclick = () => submit("allow_all");
  msgsEl.appendChild(card);
}

function lockPermCard(permId, note) {
  const card = msgsEl.querySelector('.perm-card[data-perm-id="' + permId + '"]');
  if (!card || card.classList.contains("done")) return;
  card.classList.add("done");
  card.querySelectorAll("button, input").forEach((el) => { el.disabled = true; });
  if (note) {
    const n = document.createElement("div");
    n.className = "ask-note";
    n.textContent = note;
    card.appendChild(n);
  }
}

function sysNote(text, isErr) {
  const el = document.createElement("div");
  el.className = "sys-note" + (isErr ? " err" : "");
  el.textContent = text;
  msgsEl.appendChild(el);
  scrollBottom();
}

$("#btn-send").onclick = sendMsg;
$("#btn-stop").onclick = async () => {
  if (!activeRun) return;
  const peer = activeRun.peer;
  try {
    const r = await api("/api/run/" + activeRun.id + "/stop", { method: "POST" });
    if (!peer) sysNote("已請它停下");
    else if (r && r.ok === false) sysNote(r.error || "打斷失敗", true);
  } catch (e) { sysNote("停不下來：" + e.message, true); }
};

/* ---------- 輸入框 ---------- */
function autoGrow() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 132) + "px";
}
inputEl.addEventListener("input", autoGrow);
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendMsg(); }
});

/* iOS 鍵盤把輸入列蓋住的修正 */
if (window.visualViewport) {
  const vv = window.visualViewport;
  const fix = () => {
    const gap = window.innerHeight - vv.height - vv.offsetTop;
    chatScreen.style.bottom = (gap > 0 ? gap : 0) + "px";
    scrollBottom();
  };
  vv.addEventListener("resize", fix);
  vv.addEventListener("scroll", fix);
}

/* ---------- 新聊天室：先選 AI，再選專案 ---------- */
let engines = [];

$("#btn-new").onclick = async () => {
  const sheet = $("#sheet-engine");
  sheet.classList.remove("hidden");
  const box = $("#engine-list");
  box.innerHTML = '<div class="empty-hint">載入中…</div>';
  try {
    const data = await api("/api/engines");
    engines = data.engines;
    box.innerHTML = "";
    for (const e of engines) {
      const usable = e.kind === "cli" ? e.ready : e.has_key;
      const el = document.createElement("div");
      el.className = "project-item" + (usable ? "" : " dim");
      el.innerHTML = '<div class="e-icon">' + esc(e.icon) + "</div>" +
        '<div><div class="p-name">' + esc(e.label) +
        (usable ? "" : '<span class="tag" style="margin-left:6px">未設定</span>') + "</div>" +
        '<div class="p-path">' + esc(e.note) + "</div></div>";
      el.onclick = () => {
        if (!usable) {
          sheet.classList.add("hidden");
          alert(e.kind === "cli" ? "這台電腦沒有安裝 " + e.label
            : "先到「設定 → 其他 AI」貼上 " + e.label + " 的 API key");
          return;
        }
        sheet.classList.add("hidden");
        if (e.kind === "api") {
          openRoom({ slug: null, sid: null, engine: e.id, title: "新聊天室",
                     project: "", project_name: e.label });
        } else {
          pickProject(e.id);
        }
      };
      box.appendChild(el);
    }
  } catch (e) {
    box.innerHTML = '<div class="empty-hint">' + esc(e.message) + "</div>";
  }
};

async function pickProject(engineId) {
  const sheet = $("#sheet-new");
  sheet.classList.remove("hidden");
  const box = $("#project-list");
  box.innerHTML = '<div class="empty-hint">載入中…</div>';
  try {
    const data = await api("/api/projects");
    box.innerHTML = "";
    for (const p of data.projects) {
      const el = document.createElement("div");
      el.className = "project-item";
      el.innerHTML = '<div><div class="p-name">' + esc(p.name) + '</div>' +
        '<div class="p-path">' + esc(p.path) + "</div></div>";
      el.onclick = () => {
        sheet.classList.add("hidden");
        openRoom({ slug: null, sid: null, engine: engineId, title: "新聊天室",
                   project: p.path, project_name: p.name });
      };
      box.appendChild(el);
    }
  } catch (e) {
    box.innerHTML = '<div class="empty-hint">' + esc(e.message) + "</div>";
  }
}

/* ---------- 其他 AI 的 API key ---------- */
let keysLoaded = false;

async function renderKeys() {
  const box = $("#keys-panel");
  box.innerHTML = '<div class="empty-hint">載入中…</div>';
  try {
    const data = await api("/api/engines");
    engines = data.engines;
    box.innerHTML = "";
    for (const e of engines.filter((x) => x.kind === "api")) {
      const row = document.createElement("div");
      row.className = "key-row";
      row.innerHTML =
        '<div class="key-head">' + esc(e.icon) + " " + esc(e.label) +
        (e.has_key ? '<span class="tag live">已設定</span>' : '<span class="tag">未設定</span>') + "</div>" +
        '<input class="key-in" type="password" placeholder="' +
        (e.has_key ? "已存好，要換再貼新的" : "貼上 API key") + '" autocomplete="off">' +
        '<input class="model-in" type="text" placeholder="型號" value="' + esc(e.model) + '">' +
        '<div class="key-btns"><button class="key-save">存起來</button>' +
        (e.has_key ? '<button class="key-clear">清除</button>' : "") + "</div>";
      const keyIn = row.querySelector(".key-in");
      const modelIn = row.querySelector(".model-in");
      row.querySelector(".key-save").onclick = async () => {
        try {
          await api("/api/engines/" + e.id + "/key", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key: keyIn.value || null, model: modelIn.value }),
          });
          keyIn.value = "";
          renderKeys();
        } catch (err) { alert("存不起來：" + err.message); }
      };
      const clr = row.querySelector(".key-clear");
      if (clr) clr.onclick = async () => {
        if (!confirm("清除 " + e.label + " 的 API key？")) return;
        await api("/api/engines/" + e.id + "/key", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key: "" }),
        });
        renderKeys();
      };
      box.appendChild(row);
    }
  } catch (e) {
    box.innerHTML = '<div class="empty-hint">' + esc(e.message) + "</div>";
  }
}

$("#keys-toggle").onclick = () => {
  const panel = $("#keys-panel");
  const closed = panel.classList.toggle("hidden");
  $("#keys-toggle").textContent = "Grok / Gemini / ChatGPT / DeepSeek… " + (closed ? "▾" : "▴");
  if (!closed && !keysLoaded) { keysLoaded = true; renderKeys(); }
};

/* ---------- 聊天室內調模型/力度 ---------- */
$("#btn-room").onclick = () => {
  if (!current) return;
  $("#sheet-room").classList.remove("hidden");
  bindSeg("#seg-room-model", () => roomOv("model") || "default",
    (v) => { setRoomOv("model", v); updateRoomBtn(); });
  bindSeg("#seg-room-effort", () => roomOv("effort") || "default",
    (v) => { setRoomOv("effort", v); updateRoomBtn(); });
};

/* ---------- 語音輸入 ---------- */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null, recBase = "";

function stopRec() {
  if (rec) { try { rec.stop(); } catch (e) { /* ignore */ } rec = null; }
  $("#btn-mic").classList.remove("rec");
}

$("#btn-mic").onclick = () => {
  if (rec) { stopRec(); return; }
  if (!SR) {
    sysNote("這個瀏覽器不支援語音辨識——可以改用鍵盤上的 🎤 聽寫鍵");
    return;
  }
  rec = new SR();
  rec.lang = "zh-TW";
  rec.interimResults = true;
  rec.continuous = true;
  recBase = inputEl.value ? inputEl.value + " " : "";
  rec.onresult = (e) => {
    let done = "", interim = "";
    for (const r of e.results) {
      if (r.isFinal) done += r[0].transcript;
      else interim += r[0].transcript;
    }
    inputEl.value = recBase + done + interim;
    autoGrow();
  };
  rec.onend = () => { rec = null; $("#btn-mic").classList.remove("rec"); };
  rec.onerror = (e) => {
    if (e.error === "not-allowed" || e.error === "service-not-allowed") {
      sysNote("麥克風權限被擋了——也可以用鍵盤上的 🎤 聽寫鍵", true);
    }
    stopRec();
  };
  try {
    rec.start();
    $("#btn-mic").classList.add("rec");
  } catch (e) {
    stopRec();
  }
};

/* ---------- 用量 ---------- */
function fmtTok(n) {
  if (n >= 1e9) return (n / 1e9).toFixed(1) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}

function fmtReset(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const mins = Math.round((d - Date.now()) / 60000);
  if (mins <= 0) return "即將重置";
  if (mins < 60) return mins + " 分後重置";
  if (mins < 24 * 60) return Math.floor(mins / 60) + " 小時 " + (mins % 60) + " 分後重置";
  const wd = "日一二三四五六"[d.getDay()];
  const pad = (n) => String(n).padStart(2, "0");
  return "週" + wd + " " + pad(d.getHours()) + ":" + pad(d.getMinutes()) + " 重置";
}

async function loadLimits() {
  const box = $("#limits-box");
  box.textContent = "查詢額度中…";
  try {
    const u = await api("/api/limits");
    if (!u.ok) { box.textContent = "額度查不到：" + u.error; return; }
    let html = "";
    for (const l of u.limits) {
      const cls = l.percent >= 80 ? "hot" : l.percent >= 50 ? "warn" : "";
      html += '<div class="lim-row"><div class="lim-top"><span class="l-name">' + esc(l.label) +
        '</span><span class="l-val">' + l.percent + "%" + (l.resets_at ? " · " + fmtReset(l.resets_at) : "") +
        '</span></div><div class="lim-track"><i class="' + cls + '" style="width:' +
        Math.min(100, l.percent) + '%"></i></div></div>';
    }
    if (u.credits) {
      html += '<div class="usage-row" style="margin-top:6px"><span class="u-k">加購額度</span><span class="u-v">$' +
        u.credits.used.toFixed(2) + " / $" + u.credits.limit.toFixed(2) + "</span></div>";
    }
    box.innerHTML = html || "沒有額度資料";
  } catch (e) {
    box.textContent = "額度查不到：" + e.message;
  }
}

async function loadUsage() {
  const box = $("#usage-box");
  box.textContent = "統計 token 中…（第一次會多花幾秒）";
  try {
    const u = await api("/api/usage");
    const row = (k, v) => '<div class="usage-row"><span class="u-k">' + esc(k) + '</span><span class="u-v">' + esc(v) + "</span></div>";
    const line = (d) => "回 " + d.msgs + " 次 · 輸入 " + fmtTok(d.in) + " · 輸出 " + fmtTok(d.out) + " · 快取 " + fmtTok(d.cache_read);
    let html = row("今天", line(u.today)) + row("近 7 天", line(u.window));
    const models = Object.entries(u.models).slice(0, 5);
    if (models.length) {
      html += '<div class="usage-sub">';
      for (const [name, d] of models) html += row(name, line(d));
      html += "</div>";
    }
    box.innerHTML = html;
  } catch (e) {
    box.textContent = "統計不出來：" + e.message;
  }
}

let usageLoaded = false;
$("#usage-toggle").onclick = () => {
  const panel = $("#usage-panel");
  const open = panel.classList.toggle("hidden");
  $("#usage-toggle").textContent = "額度百分比＋token 統計 " + (open ? "▾" : "▴");
  if (!open && !usageLoaded) {
    usageLoaded = true;
    loadLimits();
    loadUsage();
  } else if (!open) {
    loadLimits();
  }
};

/* ---------- 設定 ---------- */
$("#btn-settings").onclick = async () => {
  $("#sheet-settings").classList.remove("hidden");
  const bindRadios = (name, getter, key) => {
    document.querySelectorAll('input[name="' + name + '"]').forEach((r) => {
      r.checked = r.value === getter();
      r.onchange = () => localStorage.setItem(key, r.value);
    });
  };
  bindRadios("mode", mode, "cc-mode");
  bindRadios("model", modelPick, "cc-model");
  bindRadios("effort", effortPick, "cc-effort");
  bindSeg("#seg-theme", themePick, (v) => { localStorage.setItem("cc-theme", v); applyTheme(); });
  const chk = $("#chk-all");
  chk.checked = showAll();
  chk.onchange = () => {
    localStorage.setItem("cc-show-all", chk.checked ? "1" : "0");
    loadRooms();
  };
  try {
    const h = await api("/api/health");
    serverInfo = h;
    $("#health-line").textContent = "伺服器正常 · " + h.claude +
      (h.desktop_app ? " · 桌面 App：有" + (h.desktop_registry ? "（登錄檔已對上）" : "") : " · 桌面 App：沒裝");
    $("#desktop-sync-opts").classList.toggle("hidden", !h.desktop_app);
    document.querySelectorAll('input[name="dsync"]').forEach((r) => {
      r.checked = r.value === h.desktop_sync;
      r.onchange = async () => {
        try {
          const d = await api("/api/settings", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ desktop_sync: r.value }),
          });
          serverInfo.desktop_sync = d.desktop_sync;
        } catch (e) { sysNote("設定沒存成功：" + e.message, true); }
      };
    });
  } catch (e) {
    $("#health-line").textContent = "伺服器連不上：" + e.message;
  }
};

document.querySelectorAll("[data-close]").forEach((b) => {
  b.onclick = () => $("#" + b.dataset.close).classList.add("hidden");
});
document.querySelectorAll(".sheet-mask").forEach((m) => {
  m.addEventListener("click", (e) => { if (e.target === m) m.classList.add("hidden"); });
});

/* ---------- 啟動 ---------- */
$("#search").addEventListener("input", renderRooms);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    loadRooms(true);
    if (current && current.sid && !activeRun) {
      api("/api/status").then((st) => {
        const info = st.running[current.sid];
        if (info) attachRun(info.run_id, info.n_events, true, info.peer);
        else loadHistory().then(() => startWatch()).catch(() => {});
      }).catch(() => {});
    }
  }
});
roomsTimer = setInterval(() => { if (!document.hidden && !current) loadRooms(true); }, 25000);
void roomsTimer;
loadRooms();

/* ---------- 看圖層 ----------
   回覆裡的圖片原本是 <a target=_blank>；加到主畫面當 App 用時沒有瀏覽器框，
   點下去整個 App 被換成那張圖、沒有返回鍵。改成疊一層自己的看圖層：
   ✕ 或點黑底關閉、往下滑關閉、點圖切換原尺寸（頁面禁止雙指縮放，放大靠這個）。 */
const lightbox = document.createElement("div");
lightbox.className = "lightbox hidden";
lightbox.innerHTML = '<img alt=""><button class="lb-close" aria-label="關閉">✕</button>';
document.body.appendChild(lightbox);
const lbImg = lightbox.querySelector("img");
function hideLightbox() {
  lightbox.classList.add("hidden");
  lightbox.classList.remove("zoom");
  lbImg.removeAttribute("src");
}
/* 檢視層共用的瀏覽紀錄：開的時候記一筆，關的時候退回那一筆，返回鍵與手勢才會先關檢視層 */
function viewerOpen() {
  return !lightbox.classList.contains("hidden") || !docview.classList.contains("hidden");   // 只在載入完後被呼叫，docview 已建好
}
function hideViewers() {
  hideLightbox();
  hideDoc();
}
function pushViewerState() {
  if (!(history.state && history.state.viewer)) history.pushState({ chat: 1, viewer: 1 }, "");
}
function closeViewer() {
  if (history.state && history.state.viewer) history.back();   // popstate 會呼叫 hideViewers
  else hideViewers();
}
function openImage(src) {
  pushViewerState();
  lbImg.src = src;
  lightbox.classList.remove("hidden", "zoom");
  lightbox.scrollTo(0, 0);
}
msgsEl.addEventListener("click", (e) => {
  const img = e.target.closest("img");
  if (img) { e.preventDefault(); openImage(img.currentSrc || img.src); return; }
  // 其他本機檔案連結（📎 附件、🎬/🎵 檔名、程式碼區塊裡的路徑）同樣會把整個 App 換掉 → 開檢視層
  const a = e.target.closest('a[href^="/api/file?"]');
  if (!a) return;
  e.preventDefault();
  openFile(a.getAttribute("href"));
});

/* ---------- 檔案檢視層 ----------
   伺服器對 html/svg/js 刻意強制下載，不讓它們在這個網域執行；這裡也守同一條線：
   html 放進沒有 allow-scripts、沒有 allow-same-origin 的 sandbox iframe，只看排版不跑程式。 */
const docview = document.createElement("div");
docview.className = "docview hidden";
docview.innerHTML = '<header class="dv-bar"><div class="dv-title"></div><button class="dv-close" aria-label="關閉">✕</button></header><div class="dv-body"></div>';
document.body.appendChild(docview);
const dvTitle = docview.querySelector(".dv-title"), dvBody = docview.querySelector(".dv-body");
function hideDoc() {
  docview.classList.add("hidden");
  dvBody.innerHTML = "";   // 停掉影音、放掉 iframe
}
docview.querySelector(".dv-close").onclick = closeViewer;
const TEXT_CAP = 1000000;   // ponytail: 超過 100 萬字只顯示開頭，手機才不會卡死；真要看全文再加分段載入
async function openFile(url) {
  const path = new URLSearchParams(url.split("?")[1]).get("path") || "";
  const name = path.split(/[\\/]/).pop();
  const ext = (name.match(/\.([a-z0-9]+)$/i) || [, ""])[1].toLowerCase();
  if (["png", "jpg", "jpeg", "gif", "webp"].includes(ext)) { openImage(url); return; }
  pushViewerState();
  dvTitle.textContent = name;
  dvBody.className = "dv-body";
  dvBody.innerHTML = "";
  docview.classList.remove("hidden");
  if (["mp4", "mov", "webm", "m4v"].includes(ext)) {
    dvBody.classList.add("dv-media");
    dvBody.innerHTML = '<video controls playsinline autoplay src="' + url + '"></video>';
    return;
  }
  if (["mp3", "wav", "m4a"].includes(ext)) {
    dvBody.classList.add("dv-media");
    dvBody.innerHTML = '<audio controls autoplay src="' + url + '"></audio>';
    return;
  }
  if (ext === "pdf") {
    dvBody.classList.add("dv-frame");
    dvBody.innerHTML = '<iframe src="' + url + '"></iframe>';
    return;
  }
  dvBody.innerHTML = '<div class="sys-note">載入中…</div>';
  let text;
  try {
    const r = await fetch(url);
    if (!r.ok) throw new Error("HTTP " + r.status);
    text = await r.text();
  } catch (err) {
    dvBody.innerHTML = '<div class="sys-note">打不開：' + esc(err.message) + "</div>";
    return;
  }
  if (docview.classList.contains("hidden") || dvTitle.textContent !== name) return;   // 載入中就被關掉或換檔了
  const cut = text.length > TEXT_CAP;
  if (cut) text = text.slice(0, TEXT_CAP);
  const note = cut ? '<div class="sys-note">檔案太大，只顯示前 100 萬字</div>' : "";
  if (ext === "html" || ext === "htm") {
    dvBody.classList.add("dv-frame");
    const f = document.createElement("iframe");
    f.setAttribute("sandbox", "");
    f.srcdoc = text;
    dvBody.innerHTML = note;
    dvBody.appendChild(f);
    return;
  }
  if (ext === "md") {
    dvBody.innerHTML = note + '<div class="bubble dv-md">' + md(text) + "</div>";
    return;
  }
  if (ext === "json") {
    try { text = JSON.stringify(JSON.parse(text), null, 2); } catch (_) { /* 不是合法 JSON 就照原樣顯示 */ }
  }
  const pre = document.createElement("pre");
  pre.className = "dv-text";
  pre.textContent = text;
  dvBody.innerHTML = note;
  dvBody.appendChild(pre);
}
lightbox.addEventListener("click", (e) => {
  if (e.target === lbImg) lightbox.classList.toggle("zoom");
  else closeViewer();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && viewerOpen()) closeViewer();
});
(function lightboxSwipeDown() {
  let y0 = null;
  lightbox.addEventListener("touchstart", (e) => {
    y0 = lightbox.classList.contains("zoom") || e.touches.length > 1 ? null : e.touches[0].clientY;
  }, { passive: true });
  lightbox.addEventListener("touchmove", (e) => {
    if (y0 == null) return;
    const dy = Math.max(0, e.touches[0].clientY - y0);
    lbImg.style.transform = "translateY(" + dy + "px)";
    lightbox.style.opacity = String(Math.max(0.3, 1 - dy / 400));
  }, { passive: true });
  lightbox.addEventListener("touchend", (e) => {
    if (y0 == null) return;
    const dy = e.changedTouches[0].clientY - y0;
    y0 = null;
    lbImg.style.transform = "";
    lightbox.style.opacity = "";
    if (dy > 120) closeViewer();
  });
})();

/* ---------- 右滑返回 ----------
   主畫面 App 沒有系統的邊緣返回手勢。對話頁任何地方往右滑：畫面跟手，
   滑過三分之一寬或甩得夠快就回列表，不然彈回。橫向可捲的區塊（程式碼、表格）、
   輸入框、影音播放器裡不接手，免得搶它們自己的拖曳。 */
(function swipeBack() {
  let x0 = 0, y0 = 0, t0 = 0, dx = 0, mode = "off";   // off=不處理, wait=還沒判方向, drag=跟手中
  let px = 0, pt = 0, vel = 0;                          // 放開那一刻的速度（px/ms），甩動看它不看平均
  function scrollsX(el) {
    for (; el && el !== chatScreen; el = el.parentElement) {
      if (el.scrollWidth > el.clientWidth + 2) {
        const ox = getComputedStyle(el).overflowX;
        if (ox === "auto" || ox === "scroll") return true;
      }
    }
    return false;
  }
  chatScreen.addEventListener("touchstart", (e) => {
    mode = "off";
    if (e.touches.length !== 1 || !current || !lightbox.classList.contains("hidden") || !docview.classList.contains("hidden")) return;
    const t = e.target;
    if (t.closest("input, textarea, select, video, audio, .composer") || scrollsX(t)) return;
    x0 = px = e.touches[0].clientX; y0 = e.touches[0].clientY; t0 = pt = Date.now(); dx = 0; vel = 0;
    mode = "wait";
  }, { passive: true });
  chatScreen.addEventListener("touchmove", (e) => {
    if (mode === "off") return;
    const ddx = e.touches[0].clientX - x0, ddy = e.touches[0].clientY - y0;
    if (mode === "wait") {
      if (Math.abs(ddx) < 10 && Math.abs(ddy) < 10) return;
      // 往右、橫向明顯大於直向才算；按住超過半秒才動的多半是在選字
      if (!(ddx > 0 && Math.abs(ddx) > Math.abs(ddy) * 1.2 && Date.now() - t0 < 500)) { mode = "off"; return; }
      mode = "drag";
      chatScreen.style.transition = "none";
    }
    e.preventDefault();
    const x = e.touches[0].clientX, now = Date.now();
    if (now > pt) { vel = 0.7 * (x - px) / (now - pt) + 0.3 * vel; px = x; pt = now; }
    dx = Math.max(0, ddx);
    chatScreen.style.transform = "translateX(" + dx + "px)";
  }, { passive: false });
  function end() {
    if (mode !== "drag") { mode = "off"; return; }
    mode = "off";
    const fast = dx > 40 && vel > 0.5 && Date.now() - pt < 100;   // 停在半路再放開不算甩
    const leave = dx > chatScreen.clientWidth / 3 || fast;
    // 先把收合的 class 掛上，再拿掉行內樣式：轉場從手指放開的位置接著走，不會先彈回再滑走
    if (leave) chatScreen.classList.add("hidden-right");
    chatScreen.style.transition = "";
    chatScreen.style.transform = "";
    if (leave) goBack();
  }
  chatScreen.addEventListener("touchend", end);
  chatScreen.addEventListener("touchcancel", end);
})();
