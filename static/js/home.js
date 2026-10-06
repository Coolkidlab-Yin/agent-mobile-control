/* claude-chat 前端 — home：工作台（首頁）：三分頁卡片、底部導覽、連線與同步時間
   瀏覽器拿到的 /static/app.js 是伺服器把 static/js/ 依序接起來的（順序見 api.py 的 JS_PARTS）。
   資料跟清單頁共用：loadRooms()（rooms.js）打 /api/home，rooms 就是卡片（room 欄位＋section/badge），這裡只負責畫。 */
"use strict";

let homeSynced = 0;          // 最近一次成功拿到資料的本機時間（手機→伺服器那一層）
let homeFailed = "";         // 最近一次拿資料失敗的訊息；成功就清空
let homeTab = localStorage.getItem("cc-home-tab") || "todo";
let listTab = "home";        // home（工作台）／all（全部對話）
const HOME_STALE_MS = 60000; // 兩個輪詢週期沒拿到新資料＝可能過期
const homeListEl = $("#home-list");
const TAB_EMPTY = { todo: "沒有在等你的事", running: "現在沒有在跑的工作", done: "還沒有紀錄" };

function ago(epoch) {
  if (!epoch) return "";
  const s = Math.max(0, Date.now() / 1000 - epoch);
  if (s < 60) return "剛剛";
  if (s < 3600) return Math.floor(s / 60) + " 分鐘前";
  if (s < 86400) return Math.floor(s / 3600) + " 小時前";
  if (s < 86400 * 7) return Math.floor(s / 86400) + " 天前";
  return new Date(epoch * 1000).toLocaleDateString("zh-TW", { month: "numeric", day: "numeric" });
}

function renderHome() {
  const pool = projFilter === "全部" ? rooms : rooms.filter((r) => r.project_name === projFilter);
  // 分頁上的數字跟底下的卡片用同一份資料算，選了專案數字就跟著縮
  const counts = { todo: 0, running: 0, done: 0 };
  for (const r of pool) if (r.section in counts) counts[r.section]++;
  $("#home-tabs").querySelectorAll("button").forEach((b) => {
    b.classList.toggle("on", b.dataset.v === homeTab);
    const i = b.querySelector("i");
    i.textContent = counts[b.dataset.v] || 0;
    i.classList.toggle("hot", b.dataset.v === "todo" && counts.todo > 0);
  });
  // 已完成一律只看一個專案：全部專案倒在一起幾十張太多，沒有專案時改放各專案的按鈕，一點就切
  if (homeTab === "done" && projFilter === "全部" && counts.done) {
    const per = {};
    for (const r of pool) if (r.section === "done") per[r.project_name || "?"] = (per[r.project_name || "?"] || 0) + 1;
    homeListEl.innerHTML = '<div class="empty-hint pick-hint">已完成只列一個專案的對話，先選專案：</div><div class="proj-pick"></div>';
    const wrap = homeListEl.querySelector(".proj-pick");
    for (const name of Object.keys(per).sort((a, b) => per[b] - per[a])) {
      const b = document.createElement("button");
      b.className = "proj-pick-btn";
      b.innerHTML = esc(name) + "<i>" + per[name] + "</i>";
      b.onclick = () => { const sel = $("#proj-select"); sel.value = name; sel.onchange(); };
      wrap.appendChild(b);
    }
    return;
  }
  const shown = pool.filter((r) => r.section === homeTab);
  shown.sort((a, b) => (b.last_epoch || 0) - (a.last_epoch || 0));
  if (!shown.length) {
    homeListEl.innerHTML = '<div class="empty-hint">' +
      (homeFailed && !homeSynced ? "連不上伺服器：" + esc(homeFailed) : TAB_EMPTY[homeTab]) + "</div>";
    return;
  }
  homeListEl.innerHTML = "";
  for (const r of shown) homeListEl.appendChild(homeCard(r));
}

function homeCard(r) {
  const el = document.createElement("div");
  el.className = "hcard" + (r.badge_kind && r.badge_kind !== "none" ? " k-" + r.badge_kind : "");
  el.dataset.sid = r.sid;
  const eng = r.engine || "claude";
  const initial = (r.project_name || "?").slice(0, 1).toUpperCase();
  const body = r.note || r.preview || "";
  const tags =
    (r.live ? '<span class="tag live">桌機開著</span>' : "") +
    (eng !== "claude" ? '<span class="tag eng">' + esc(ENGINE_NAME[eng] || eng) + "</span>" : "") +
    (eng === "claude" && r.app && !r.desktop ? '<span class="tag phone">只在手機</span>' : "") +
    (r.archived ? '<span class="tag arch">封存</span>' : "");
  const cta = r.badge_kind === "ask" ? "去回答" : r.badge_kind === "perm" ? "去授權" : "";
  el.innerHTML =
    '<div class="hc-head">' +
      '<div class="hc-icon' + (eng !== "claude" ? " eng-" + eng : "") + '" style="--h:' + hueFor(r.project_name) + '">' +
        esc(eng === "claude" ? initial : (ENGINE_ICON[eng] || "?")) + "</div>" +
      '<div class="hc-main"><div class="hc-title">' + esc(r.title || "新聊天室") + "</div>" +
        '<div class="hc-meta">' + esc(r.project_name || "") + " · " + esc(ago(r.last_epoch)) + "更新</div></div>" +
      (r.badge ? '<span class="badge ' + esc(r.badge_kind) + '">' + esc(r.badge) + "</span>" : "") +
    "</div>" +
    (body ? '<div class="hc-body' + (r.note ? "" : " dim") + '">' + esc(body) + "</div>" : "") +
    (tags ? '<div class="hc-tags">' + tags + "</div>" : "") +
    (cta ? '<button class="hc-cta">' + cta + "</button>" : "");
  el.onclick = () => openRoom(r);
  el.oncontextmenu = (e) => { e.preventDefault(); openActions(r); };
  return el;
}

/* 連線三層裡的第一層：手機 → 伺服器。綠＝剛同步過；橘＝超過兩個輪詢週期沒拿到新資料；紅＝最近一次拿失敗 */
function renderConn() {
  const el = $("#home-conn");
  if (!el) return;
  if (!homeSynced) { el.innerHTML = homeFailed ? '<i class="conn-dot bad"></i>連不上伺服器' : "連線中…"; return; }
  const t = new Date(homeSynced).toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit" });
  if (homeFailed) el.innerHTML = '<i class="conn-dot bad"></i>連不上伺服器 · 上次同步 ' + t;
  else if (Date.now() - homeSynced > HOME_STALE_MS) el.innerHTML = '<i class="conn-dot stale"></i>資料可能過期 · 上次同步 ' + t;
  else el.innerHTML = '<i class="conn-dot"></i>已連線 · 同步 ' + t;
}
setInterval(renderConn, 10000);

$("#home-tabs").querySelectorAll("button").forEach((b) => {
  b.onclick = () => { homeTab = b.dataset.v; localStorage.setItem("cc-home-tab", homeTab); renderHome(); };
});

function setListTab(tab) {
  listTab = tab;
  $("#pane-home").classList.toggle("hidden", tab !== "home");
  $("#pane-all").classList.toggle("hidden", tab !== "all");
  $("#list-title").textContent = tab === "home" ? "工作台" : "全部對話";
  $("#bottom-nav").querySelectorAll("button[data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
}
$("#bottom-nav").querySelectorAll("button[data-tab]").forEach((b) => {
  if (b.dataset.tab === "settings") return;   // 設定鈕是 #btn-settings，由 sheets.js 綁
  b.onclick = () => setListTab(b.dataset.tab);
});
