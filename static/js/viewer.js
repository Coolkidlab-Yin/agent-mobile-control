/* claude-chat 前端 — viewer：看圖層、檔案檢視層、右滑返回
   拆自 app.js；瀏覽器拿到的 /static/app.js 是伺服器把 static/js/ 依序接起來的（順序見 api.py 的 JS_PARTS）。 */
"use strict";

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
/* 差異檢視（第二階段）：抓 patch 端點的純文字，逐行上色；跟檔案檢視層共用同一層、同一套返回規則 */
function renderDiff(text) {
  const pre = document.createElement("pre");
  pre.className = "dv-text dv-diff";
  for (const line of text.split("\n")) {
    const span = document.createElement("span");
    span.className = line.startsWith("+") && !line.startsWith("+++") ? "d-add"
                   : line.startsWith("-") && !line.startsWith("---") ? "d-del"
                   : line.startsWith("@@") || line.startsWith("###") ? "d-hunk"
                   : line.startsWith("diff ") || line.startsWith("index ") || line.startsWith("+++") || line.startsWith("---") ? "d-meta" : "";
    span.textContent = line + "\n";
    pre.appendChild(span);
  }
  return pre;
}
async function openDiff(name, url) {
  pushViewerState();
  dvTitle.textContent = name;
  dvBody.className = "dv-body";
  dvBody.innerHTML = '<div class="sys-note">載入中…</div>';
  docview.classList.remove("hidden");
  let text;
  try {
    const r = await fetch(url);
    if (!r.ok) throw new Error("HTTP " + r.status);
    text = await r.text();
  } catch (err) {
    dvBody.innerHTML = '<div class="sys-note">打不開：' + esc(err.message) + "</div>";
    return;
  }
  if (docview.classList.contains("hidden") || dvTitle.textContent !== name) return;
  dvBody.innerHTML = "";
  dvBody.appendChild(renderDiff(text));
}
function openTextView(name, text) {
  pushViewerState();
  dvTitle.textContent = name;
  dvBody.className = "dv-body";
  docview.classList.remove("hidden");
  const pre = document.createElement("pre");
  pre.className = "dv-text";
  pre.textContent = text;
  dvBody.innerHTML = "";
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
