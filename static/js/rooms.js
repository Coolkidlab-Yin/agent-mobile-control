/* claude-chat 前端 — rooms：聊天室清單：載入、專案篩選、全文搜尋、左滑／長按動作
   拆自 app.js；瀏覽器拿到的 /static/app.js 是伺服器把 static/js/ 依序接起來的（順序見 api.py 的 JS_PARTS）。 */
"use strict";

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
