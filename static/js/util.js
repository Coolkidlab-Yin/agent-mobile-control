/* claude-chat 前端 — util：小工具（DOM 選取、跳脫、時間、極簡 markdown、檔案路徑變預覽、呼叫 API、數字格式）
   拆自 app.js；瀏覽器拿到的 /static/app.js 是伺服器把 static/js/ 依序接起來的（順序見 api.py 的 JS_PARTS）。 */
"use strict";

const $ = (s) => document.querySelector(s);

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
      '<a class="file-link" href="' + url + '" target="_blank">' + icon("film") + ' ' + esc(base) + "</a>";
  }
  if (["png", "jpg", "jpeg", "gif", "webp"].includes(e)) {
    return '<a href="' + url + '" target="_blank"><img src="' + url + '" loading="lazy"></a>';
  }
  if (["mp3", "wav", "m4a"].includes(e)) {
    return '<audio controls preload="none" src="' + url + '"></audio>' +
      '<a class="file-link" href="' + url + '" target="_blank">' + icon("music") + ' ' + esc(base) + "</a>";
  }
  return '<a class="file-link" href="' + url + '" target="_blank">' + icon("clip") + ' ' + esc(base) + "</a>";
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

/* ---------- 圖示 ----------
   介面不用 emoji（各平台長得不一樣、彩色跟配色打架）：一套細線圖示，筆畫跟著文字顏色走，深色模式自動適用。
   路徑取自 Lucide（ISC 授權），24×24、圓角筆畫。HTML 裡寫 data-icon="名字" 的元素，載入時自動補上圖示。 */
const ICONS = {
  home: '<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  settings: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  monitor: '<rect width="20" height="14" x="2" y="3" rx="2"/><path d="M8 21h8M12 17v4"/>',
  pencil: '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/>',
  archive: '<rect width="20" height="5" x="2" y="3" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/>',
  trash: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  relay: '<path d="m16 3 4 4-4 4"/><path d="M20 7H4"/><path d="m8 21-4-4 4-4"/><path d="M4 17h16"/>',
  help: '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
  lock: '<rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  alert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  doc: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8M16 17H8M10 9H8"/>',
  wrench: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
  film: '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 3v18M17 3v18M3 7.5h4M3 12h18M3 16.5h4M17 7.5h4M17 16.5h4"/>',
  music: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  clip: '<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
  mic: '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/>',
  terminal: '<path d="m4 17 6-6-6-6"/><path d="M12 19h8"/>',
  bot: '<path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/>',
  plus: '<path d="M5 12h14M12 5v14"/>',
  flow: '<rect width="8" height="8" x="3" y="3" rx="2"/><path d="M7 11v4a2 2 0 0 0 2 2h4"/><rect width="8" height="8" x="13" y="13" rx="2"/>',
};
function icon(name) {
  return '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">' + (ICONS[name] || "") + "</svg>";
}
document.querySelectorAll("[data-icon]").forEach((el) => el.insertAdjacentHTML("afterbegin", icon(el.dataset.icon)));

/* 桌面 app 的子對話（手動分出的 fork／旁開的子任務）：排在父對話正下方，回 [[room, 深度], ...]。
   父對話不在這份清單（別的分頁、被封存）就照原位置、深度 0，卡片上改寫「從哪個對話分出」。 */
function nestForks(list) {
  const inList = new Set(list.map((r) => r.sid));
  const kids = {};
  for (const r of list) {
    if (r.parent_sid && r.parent_sid !== r.sid && inList.has(r.parent_sid)) (kids[r.parent_sid] = kids[r.parent_sid] || []).push(r);
  }
  const out = [], seen = new Set();
  const add = (r, depth) => {
    if (seen.has(r.sid)) return;
    seen.add(r.sid);
    out.push([r, depth]);
    for (const k of kids[r.sid] || []) add(k, depth + 1);
  };
  for (const r of list) if (!(r.parent_sid && inList.has(r.parent_sid))) add(r, 0);
  for (const r of list) add(r, 1);   // 互相指來指去的怪資料也不會漏掉
  return out;
}

function forkTag(r, depth) {
  const kind = r.child_kind === "fork" ? "分支" : r.child_kind === "side" ? "子任務" : "";
  if (!kind) return "";
  const from = !depth && r.parent_title ? "：從「" + r.parent_title.slice(0, 16) + "」" : "";
  return '<span class="tag fork">' + esc(kind + from) + "</span>";
}
