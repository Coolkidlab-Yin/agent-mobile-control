/* claude-chat 前端 — chat：對話頁：進出房間、歷史訊息、旁觀桌面、送訊息與事件流、附件、輸入框、語音
   拆自 app.js；瀏覽器拿到的 /static/app.js 是伺服器把 static/js/ 依序接起來的（順序見 api.py 的 JS_PARTS）。 */
"use strict";

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

function showPendingPerms(d) {
  for (const p of d.pending || []) {
    if (!msgsEl.querySelector('.perm-card[data-perm-id="' + p.perm_id + '"]')) { hideTyping(); renderPermCard(p); scrollBottom(); }
  }
  for (const p of d.answered || []) lockPermCard(p.perm_id, permNote(p.answer, p.by, p.tool));
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
    showPendingPerms(d);   // 要交給事件流之前先放：事件流從 n_events 接起，中間送出的卡不會重播
    if (d.running) {
      // 手機這邊起的工作（例如背景重連）→ 交給事件流
      const st = await api("/api/status").catch(() => null);
      const info = st && st.running[sid];
      if (info) { attachRun(info.run_id, info.n_events, true, info.peer); return; }
    }
    watchOffset = d.offset;
    if (d.items && d.items.length) applyTailItems(d.items);
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
  startBgPoll();
  if (room.sid) {
    loadHistory().then((data) => {
      // 若這個房間有背景工作進行中 → 接上事件流；否則旁觀桌面那邊的進度
      // （run 資訊由 history 一起帶回，省掉一趟 /api/status——手機常在慢連線上）
      const info = data && data.run;
      // 先把已經在等的卡片放上來：接即時連線是從 n_events 之後開始，早先送出的卡不會再來一次
      if (data) showPendingPerms(data);
      if (info) attachRun(info.run_id, info.n_events, true, info.peer);
      else startWatch();
    }).catch(() => startWatch());
  } else {
    msgsEl.innerHTML = '<div class="sys-note">新聊天室（' + esc(room.project_name) + '）— 送出第一句就開始</div>';
  }
}

function closeRoom() {
  hideViewers();
  stopBgPoll();
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

/* iOS 鍵盤把輸入列蓋住的修正：鍵盤開著時把聊天畫面底邊推到鍵盤上緣。
 * 只在有輸入框拿到焦點時才推，沒焦點一律歸零，並在回到前景、焦點變動時重算。
 * 2026-10-04 事故：App 從背景回來時 iOS 沒再發 resize，舊的推高值卡住，
 * 房間只剩上面一截、底下露出列表，換房間也一樣（值掛在共用的聊天畫面上）。 */
if (window.visualViewport) {
  const vv = window.visualViewport;
  const NOT_TEXT = /^(button|checkbox|radio|submit|reset|file|range|color|image|hidden)$/;
  const typing = () => {
    const el = document.activeElement;
    if (!el) return false;
    if (el.tagName === "TEXTAREA" || el.isContentEditable) return true;
    return el.tagName === "INPUT" && !NOT_TEXT.test(el.type);
  };
  let lastGap = 0;
  const fix = () => {
    let gap = 0;
    if (typing()) {
      // ponytail: 上限 60% 螢幕高。真的鍵盤不會更高，超過代表讀到壞值（剛從背景回來、畫面被縮放）。
      gap = Math.max(0, Math.min(window.innerHeight - vv.height - vv.offsetTop, window.innerHeight * 0.6));
    }
    chatScreen.style.bottom = gap + "px";
    if (gap !== lastGap) scrollBottom();
    lastGap = gap;
  };
  vv.addEventListener("resize", fix);
  vv.addEventListener("scroll", fix);
  document.addEventListener("focusin", fix);
  document.addEventListener("focusout", () => setTimeout(fix, 60));
  document.addEventListener("visibilitychange", fix);
  window.addEventListener("pageshow", fix);
}

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
