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
