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
  const cta = r.badge_kind === "ask" ? "去回答" : r.badge_kind === "perm" ? "去授權" : r.badge_kind === "next" ? "去回話" : "";
  el.innerHTML =
    '<div class="hc-head">' +
      '<div class="hc-icon' + (eng !== "claude" ? " eng-" + eng : "") + '" style="--h:' + hueFor(r.project_name) + '">' +
        esc(eng === "claude" ? initial : (ENGINE_ICON[eng] || "?")) + "</div>" +
      '<div class="hc-main"><div class="hc-title">' + esc(r.title || "新聊天室") + "</div>" +
        '<div class="hc-meta">' + esc(r.project_name || "") + " · " + esc(ago(r.last_epoch)) + "更新</div></div>" +
      (r.badge ? '<span class="badge ' + esc(r.badge_kind) + '">' + esc(r.badge) + "</span>" : "") +
    "</div>" +
    (body ? '<div class="hc-body' + (r.note ? "" : " dim") + '">' + esc(body) + "</div>" : "") +
    (r.changes ? '<button class="hc-changes">' + changesLine(r.changes) + " ›</button>" : "") +
    (tags ? '<div class="hc-tags">' + tags + "</div>" : "") +
    (cta ? '<div class="hc-actions"><button class="hc-cta">' + cta + "</button>" +
           (r.badge_kind === "next" ? '<button class="hc-ack">沒事了</button>' : "") + "</div>" : "");
  el.onclick = () => openRoom(r);
  el.oncontextmenu = (e) => { e.preventDefault(); openActions(r); };
  const chg = el.querySelector(".hc-changes");
  if (chg) chg.onclick = (e) => { e.stopPropagation(); openChanges(r); };
  const ack = el.querySelector(".hc-ack");
  if (ack) ack.onclick = async (e) => {
    // 「等你回話」是從最後一句猜的；按了就記到伺服器，這句以前的不再算，之後有新回覆會再出現
    e.stopPropagation();
    ack.disabled = true;
    try {
      await api("/api/home/ack", { method: "POST", headers: { "Content-Type": "application/json" },
                                   body: JSON.stringify({ sid: r.sid, upto: r.last_epoch }) });
    } catch (err) { ack.disabled = false; ack.textContent = "沒記到：" + err.message; return; }
    loadRooms();
  };
  return el;
}

/* ---------- 成果與變更（第二階段）：卡片一行 → 抽屜 → 整份差異 ----------
   每個字都有來源：改了幾個檔來自對話紀錄的 Edit/Write；測試那段來自紀錄裡的指令結果；
   新鮮度只說「之後又改過／沒再改」，沒有指紋就不說。 */
const FRESH = { stale: "，之後又改過", current: "，之後沒再改", unverified: "" };

function changesLine(c) {
  let s = "";
  if (c.n_files) s += "✎ " + c.n_files + " 個檔";
  if (c.test) s += (s ? " · " : "") + (c.test.ok ? "✓ " : "✗ ") + esc(c.test.verdict) + (FRESH[c.test.freshness] || "");
  return s;
}

function tsClock(ts) {
  return ts ? new Date(ts * 1000).toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit" }) : "";
}

async function openChanges(r) {
  const mask = $("#sheet-changes"), body = $("#chg-body");
  $("#chg-title").textContent = r.title || "成果與變更";
  $("#chg-open").onclick = () => { mask.classList.add("hidden"); openRoom(r); };
  body.innerHTML = '<div class="empty-hint">載入中…</div>';
  mask.classList.remove("hidden");
  let d;
  try { d = await api("/api/changes/" + r.slug + "/" + r.sid); }
  catch (e) { body.innerHTML = '<div class="empty-hint">拿不到：' + esc(e.message) + "</div>"; return; }
  const base = "/api/changes/" + r.slug + "/" + r.sid + "/patch?";
  const repos = d.repos || [], many = repos.length > 1;   // 一個對話可能改到好幾個 repo（在工作區根目錄開的對話尤其如此）
  const gitUrl = (root, rel) => base + "src=git&root=" + encodeURIComponent(root) + "&rel=" + encodeURIComponent(rel);
  let h = "";
  // 1. 本對話改過的檔
  h += '<div class="setting-label">本對話改過的檔案（' + d.edits.length + "）</div>";
  if (!d.edits.length) h += '<div class="chg-empty">這個對話沒有改任何檔</div>';
  for (const e of d.edits) {
    const g = e.git;
    let st = "";
    if (!e.exists) st = "已不存在";
    else if (g) st = g.untracked ? "新檔，未加進 git" : g.status === "clean" ? "跟 HEAD 一樣" : "+" + (g.add ?? "?") + " −" + (g.del ?? "?");
    else st = "不在 git 裡";
    if (g && many) st += " · " + e.root.split("/").pop();
    const target = g && !g.untracked && g.status !== "clean" ? gitUrl(e.root, e.rel)
                   : base + "src=session&path=" + encodeURIComponent(e.path);
    h += '<button class="chg-row" data-url="' + esc(target) + '" data-name="' + esc(e.name) + '">' +
         '<span class="chg-name">' + esc(e.name) + "</span>" +
         '<span class="chg-sub">' + esc(e.kinds.join("/")) + " × " + e.n + " · " + esc(tsClock(e.last_ts)) + (st ? " · " + esc(st) : "") + "</span></button>";
  }
  // 2. 工作區其他變更：每個 repo 一段
  for (const rp of repos) {
    if (!rp.other.count) continue;
    h += '<div class="setting-label">' + esc(rp.name) + " 裡其他變更（" + rp.other.count + "，不一定跟這個對話有關）</div>";
    for (const rel of rp.other.files) {
      h += '<button class="chg-row" data-url="' + esc(gitUrl(rp.root, rel)) + '" data-name="' + esc(rel) + '">' +
           '<span class="chg-name">' + esc(rel) + "</span></button>";
    }
    if (rp.other.count > rp.other.files.length) h += '<div class="chg-empty">還有 ' + (rp.other.count - rp.other.files.length) + " 個沒列</div>";
  }
  // 3. 提交
  let commits = (d.commits || []).map((c) => (c.verified ? "✓ " : "? ") + c.sha + " " + c.subject);
  for (const rp of repos) {
    const cs = rp.commits_since.slice(0, 8);   // 跑了好幾天的對話每個 repo 都有一長串，抽屜只放前幾筆
    commits = commits.concat(cs.map((c) => "· " + (many ? rp.name + " " : "") + c.sha + " " + c.subject));
    if (rp.commits_since.length > cs.length) commits.push("· " + (many ? rp.name + " " : "") + "…還有 " + (rp.commits_since.length - cs.length) + " 筆（對話期間）");
  }
  if (commits.length) {
    h += '<div class="setting-label">提交（✓＝輸出裡的 sha 在 git 查得到；·＝對話期間 repo 裡出現的）</div>';
    for (const c of commits) h += '<div class="chg-text">' + esc(c) + "</div>";
  }
  // 4. 測試
  h += '<div class="setting-label">測試（最近 ' + d.tests.length + " 次）</div>";
  if (!d.tests.length) h += '<div class="chg-empty">這個對話沒有跑過測試指令</div>';
  for (const t of d.tests.slice().reverse()) {
    h += '<button class="chg-row" data-text="' + esc(t.tail) + '" data-name="' + esc(t.command.slice(0, 60)) + '">' +
         '<span class="chg-name">' + (t.ok ? "✓ " : "✗ ") + esc(t.verdict) + (FRESH[t.freshness] || "") + "</span>" +
         '<span class="chg-sub">' + esc(tsClock(t.ts)) + " · 指令結束狀態 " + (t.exit_code == null ? "不明" : "exit " + t.exit_code) +
         " · " + esc(t.command.slice(0, 80)) + "</span></button>";
  }
  body.innerHTML = h;
  body.querySelectorAll(".chg-row").forEach((b) => {
    b.onclick = () => {
      if (b.dataset.url) openDiff(b.dataset.name, b.dataset.url);
      else openTextView(b.dataset.name, b.dataset.text);
    };
  });
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
