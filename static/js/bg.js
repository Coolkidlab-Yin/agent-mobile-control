/* claude-chat 前端 — bg：背景任務狀態列與清單
   拆自 app.js；瀏覽器拿到的 /static/app.js 是伺服器把 static/js/ 依序接起來的（順序見 api.py 的 JS_PARTS）。 */
"use strict";

/* ---------- 背景任務 ----------
   對話在背景跑的指令／子代理／工作流程，手機原本看不到，只會覺得「怎麼卡住了」。
   開著房間時每 5 秒問一次伺服器（伺服器是增量讀紀錄，便宜）；頂端一條狀態列，點開看清單與最近輸出。 */
let bgTimer = null, bgData = [], bgNow = 0;
const BG_KIND = { bash: "⌘ 指令", agent: "🤖 子代理", workflow: "🧩 工作流程" };
const BG_STATUS = { running: "進行中", completed: "完成", failed: "失敗", killed: "已停止", stopped: "已停止", unknown: "狀態不明" };
function bgAgo(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return sec + " 秒";
  if (sec < 3600) return Math.round(sec / 60) + " 分鐘";
  const h = (sec / 3600).toFixed(1);
  return (h.endsWith(".0") ? h.slice(0, -2) : h) + " 小時";
}
async function pollBg() {
  const room = current;
  if (!room || !room.sid || (room.engine || "claude") !== "claude" || document.hidden) return;
  let d;
  try { d = await api("/api/bg/" + room.slug + "/" + room.sid); } catch (_) { return; }
  if (current !== room) return;   // 等回應時已經換房
  bgData = d.tasks || [];
  bgNow = d.now || Date.now() / 1000;
  renderBg();
}
function renderBg() {
  const chip = $("#bg-chip");
  const run = bgData.filter((t) => t.status === "running");
  chip.classList.toggle("hidden", !bgData.length);
  chip.classList.toggle("on", run.length > 0);
  // 進行中的任務裡最久沒動靜的那個：這通常就是「為什麼卡住」的答案
  const quiet = run.length ? Math.max(...run.map((t) => bgNow - (t.last || t.started))) : 0;
  chip.classList.toggle("quiet", quiet > 120);
  chip.textContent = run.length
    ? "⏳ " + run.length + " 個背景任務進行中" + (quiet > 120 ? "，最久已 " + bgAgo(quiet) + "沒動靜" : "") + " ›"
    : "背景任務 " + bgData.length + " 個，都已結束 ›";
  if (!$("#sheet-bg").classList.contains("hidden")) renderBgSheet();
}
function renderBgSheet() {
  $("#bg-list").innerHTML = bgData.map((t) => {
    const run = t.status === "running";
    const time = run
      ? "已跑 " + bgAgo(bgNow - t.started) + (t.last ? " · 最後動靜 " + bgAgo(bgNow - t.last) + "前" : "")
      : t.ended
        ? bgAgo(bgNow - t.ended) + "前結束 · 花了 " + bgAgo(t.ended - t.started)
        : bgAgo(bgNow - t.started) + "前開始";
    return '<div class="bg-item">' +
      '<div class="bg-head"><span>' + (BG_KIND[t.kind] || esc(t.kind)) + "</span>" +
      '<span class="bg-st st-' + esc(t.status) + '">' + (BG_STATUS[t.status] || esc(t.status)) + "</span></div>" +
      '<div class="bg-label">' + esc(t.label) + "</div>" +
      '<div class="bg-time">' + time + "</div>" +
      (t.status === "unknown" ? '<div class="bg-note">開它的那次對話程序已經結束、也沒收到完成通知，多半已跟著停掉</div>' : "") +
      (t.summary ? '<div class="bg-sum">' + esc(t.summary) + "</div>" : "") +
      (t.tail && t.tail.length ? '<pre class="bg-tail">' + esc(t.tail.join("\n")) + "</pre>" : "") +
      "</div>";
  }).join("") || '<div class="sys-note">這個對話目前沒有背景任務</div>';
}
function startBgPoll() {
  stopBgPoll();
  pollBg();
  bgTimer = setInterval(pollBg, 5000);
}
function stopBgPoll() {
  if (bgTimer) clearInterval(bgTimer);
  bgTimer = null;
  bgData = [];
  renderBg();
  $("#sheet-bg").classList.add("hidden");
}
$("#bg-chip").onclick = () => {
  renderBgSheet();
  $("#sheet-bg").classList.remove("hidden");
  pollBg();
};
