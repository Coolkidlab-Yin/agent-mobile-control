/* claude-chat 前端 — state：共用狀態、設定值讀寫、主題、模型清單
   拆自 app.js；瀏覽器拿到的 /static/app.js 是伺服器把 static/js/ 依序接起來的（順序見 api.py 的 JS_PARTS）。 */
"use strict";

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
