/* claude-chat 前端 — main：啟動：事件綁定與第一次載入（必須最後載入）
   拆自 app.js；瀏覽器拿到的 /static/app.js 是伺服器把 static/js/ 依序接起來的（順序見 api.py 的 JS_PARTS）。 */
"use strict";

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
