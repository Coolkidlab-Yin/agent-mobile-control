/* claude-chat 前端 — sheets：底部面板：開新聊天室、API key、房間模型／力度、額度與用量、設定
   拆自 app.js；瀏覽器拿到的 /static/app.js 是伺服器把 static/js/ 依序接起來的（順序見 api.py 的 JS_PARTS）。 */
"use strict";

/* ---------- 新聊天室：先選 AI，再選專案 ---------- */
let engines = [];

$("#btn-new").onclick = async () => {
  const sheet = $("#sheet-engine");
  sheet.classList.remove("hidden");
  const box = $("#engine-list");
  box.innerHTML = '<div class="empty-hint">載入中…</div>';
  try {
    const data = await api("/api/engines");
    engines = data.engines;
    box.innerHTML = "";
    for (const e of engines) {
      const usable = e.kind === "cli" ? e.ready : e.has_key;
      const el = document.createElement("div");
      el.className = "project-item" + (usable ? "" : " dim");
      el.innerHTML = '<div class="e-icon">' + esc(e.icon) + "</div>" +
        '<div><div class="p-name">' + esc(e.label) +
        (usable ? "" : '<span class="tag" style="margin-left:6px">未設定</span>') + "</div>" +
        '<div class="p-path">' + esc(e.note) + "</div></div>";
      el.onclick = () => {
        if (!usable) {
          sheet.classList.add("hidden");
          alert(e.kind === "cli" ? "這台電腦沒有安裝 " + e.label
            : "先到「設定 → 其他 AI」貼上 " + e.label + " 的 API key");
          return;
        }
        sheet.classList.add("hidden");
        if (e.kind === "api") {
          openRoom({ slug: null, sid: null, engine: e.id, title: "新聊天室",
                     project: "", project_name: e.label });
        } else {
          pickProject(e.id);
        }
      };
      box.appendChild(el);
    }
  } catch (e) {
    box.innerHTML = '<div class="empty-hint">' + esc(e.message) + "</div>";
  }
};

async function pickProject(engineId) {
  const sheet = $("#sheet-new");
  sheet.classList.remove("hidden");
  const box = $("#project-list");
  box.innerHTML = '<div class="empty-hint">載入中…</div>';
  try {
    const data = await api("/api/projects");
    box.innerHTML = "";
    for (const p of data.projects) {
      const el = document.createElement("div");
      el.className = "project-item";
      el.innerHTML = '<div><div class="p-name">' + esc(p.name) + '</div>' +
        '<div class="p-path">' + esc(p.path) + "</div></div>";
      el.onclick = () => {
        sheet.classList.add("hidden");
        openRoom({ slug: null, sid: null, engine: engineId, title: "新聊天室",
                   project: p.path, project_name: p.name });
      };
      box.appendChild(el);
    }
  } catch (e) {
    box.innerHTML = '<div class="empty-hint">' + esc(e.message) + "</div>";
  }
}

/* ---------- 其他 AI 的 API key ---------- */
let keysLoaded = false;

async function renderKeys() {
  const box = $("#keys-panel");
  box.innerHTML = '<div class="empty-hint">載入中…</div>';
  try {
    const data = await api("/api/engines");
    engines = data.engines;
    box.innerHTML = "";
    for (const e of engines.filter((x) => x.kind === "api")) {
      const row = document.createElement("div");
      row.className = "key-row";
      row.innerHTML =
        '<div class="key-head">' + esc(e.icon) + " " + esc(e.label) +
        (e.has_key ? '<span class="tag live">已設定</span>' : '<span class="tag">未設定</span>') + "</div>" +
        '<input class="key-in" type="password" placeholder="' +
        (e.has_key ? "已存好，要換再貼新的" : "貼上 API key") + '" autocomplete="off">' +
        '<input class="model-in" type="text" placeholder="型號" value="' + esc(e.model) + '">' +
        '<div class="key-btns"><button class="key-save">存起來</button>' +
        (e.has_key ? '<button class="key-clear">清除</button>' : "") + "</div>";
      const keyIn = row.querySelector(".key-in");
      const modelIn = row.querySelector(".model-in");
      row.querySelector(".key-save").onclick = async () => {
        try {
          await api("/api/engines/" + e.id + "/key", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key: keyIn.value || null, model: modelIn.value }),
          });
          keyIn.value = "";
          renderKeys();
        } catch (err) { alert("存不起來：" + err.message); }
      };
      const clr = row.querySelector(".key-clear");
      if (clr) clr.onclick = async () => {
        if (!confirm("清除 " + e.label + " 的 API key？")) return;
        await api("/api/engines/" + e.id + "/key", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key: "" }),
        });
        renderKeys();
      };
      box.appendChild(row);
    }
  } catch (e) {
    box.innerHTML = '<div class="empty-hint">' + esc(e.message) + "</div>";
  }
}

$("#keys-toggle").onclick = () => {
  const panel = $("#keys-panel");
  const closed = panel.classList.toggle("hidden");
  $("#keys-toggle").textContent = "Grok / Gemini / ChatGPT / DeepSeek… " + (closed ? "▾" : "▴");
  if (!closed && !keysLoaded) { keysLoaded = true; renderKeys(); }
};

/* ---------- 聊天室內調模型/力度 ---------- */
$("#btn-room").onclick = () => {
  if (!current) return;
  $("#sheet-room").classList.remove("hidden");
  bindSeg("#seg-room-model", () => roomOv("model") || "default",
    (v) => { setRoomOv("model", v); updateRoomBtn(); });
  bindSeg("#seg-room-effort", () => roomOv("effort") || "default",
    (v) => { setRoomOv("effort", v); updateRoomBtn(); });
};

async function loadLimits() {
  const box = $("#limits-box");
  box.textContent = "查詢額度中…";
  try {
    const u = await api("/api/limits");
    if (!u.ok) { box.textContent = "額度查不到：" + u.error; return; }
    let html = "";
    for (const l of u.limits) {
      const cls = l.percent >= 80 ? "hot" : l.percent >= 50 ? "warn" : "";
      html += '<div class="lim-row"><div class="lim-top"><span class="l-name">' + esc(l.label) +
        '</span><span class="l-val">' + l.percent + "%" + (l.resets_at ? " · " + fmtReset(l.resets_at) : "") +
        '</span></div><div class="lim-track"><i class="' + cls + '" style="width:' +
        Math.min(100, l.percent) + '%"></i></div></div>';
    }
    if (u.credits) {
      html += '<div class="usage-row" style="margin-top:6px"><span class="u-k">加購額度</span><span class="u-v">$' +
        u.credits.used.toFixed(2) + " / $" + u.credits.limit.toFixed(2) + "</span></div>";
    }
    box.innerHTML = html || "沒有額度資料";
  } catch (e) {
    box.textContent = "額度查不到：" + e.message;
  }
}

async function loadUsage() {
  const box = $("#usage-box");
  box.textContent = "統計 token 中…（第一次會多花幾秒）";
  try {
    const u = await api("/api/usage");
    const row = (k, v) => '<div class="usage-row"><span class="u-k">' + esc(k) + '</span><span class="u-v">' + esc(v) + "</span></div>";
    const line = (d) => "回 " + d.msgs + " 次 · 輸入 " + fmtTok(d.in) + " · 輸出 " + fmtTok(d.out) + " · 快取 " + fmtTok(d.cache_read);
    let html = row("今天", line(u.today)) + row("近 7 天", line(u.window));
    const models = Object.entries(u.models).slice(0, 5);
    if (models.length) {
      html += '<div class="usage-sub">';
      for (const [name, d] of models) html += row(name, line(d));
      html += "</div>";
    }
    box.innerHTML = html;
  } catch (e) {
    box.textContent = "統計不出來：" + e.message;
  }
}

let usageLoaded = false;
$("#usage-toggle").onclick = () => {
  const panel = $("#usage-panel");
  const open = panel.classList.toggle("hidden");
  $("#usage-toggle").textContent = "額度百分比＋token 統計 " + (open ? "▾" : "▴");
  if (!open && !usageLoaded) {
    usageLoaded = true;
    loadLimits();
    loadUsage();
  } else if (!open) {
    loadLimits();
  }
};

/* ---------- 設定 ---------- */
$("#btn-settings").onclick = async () => {
  $("#sheet-settings").classList.remove("hidden");
  const bindRadios = (name, getter, key) => {
    document.querySelectorAll('input[name="' + name + '"]').forEach((r) => {
      r.checked = r.value === getter();
      r.onchange = () => localStorage.setItem(key, r.value);
    });
  };
  bindRadios("mode", mode, "cc-mode");
  bindRadios("model", modelPick, "cc-model");
  bindRadios("effort", effortPick, "cc-effort");
  bindSeg("#seg-theme", themePick, (v) => { localStorage.setItem("cc-theme", v); applyTheme(); });
  const chk = $("#chk-all");
  chk.checked = showAll();
  chk.onchange = () => {
    localStorage.setItem("cc-show-all", chk.checked ? "1" : "0");
    loadRooms();
  };
  try {
    const h = await api("/api/health");
    serverInfo = h;
    $("#health-line").textContent = "伺服器正常 · " + h.claude +
      (h.desktop_app ? " · 桌面 App：有" + (h.desktop_registry ? "（登錄檔已對上）" : "") : " · 桌面 App：沒裝");
    $("#desktop-sync-opts").classList.toggle("hidden", !h.desktop_app);
    document.querySelectorAll('input[name="dsync"]').forEach((r) => {
      r.checked = r.value === h.desktop_sync;
      r.onchange = async () => {
        try {
          const d = await api("/api/settings", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ desktop_sync: r.value }),
          });
          serverInfo.desktop_sync = d.desktop_sync;
        } catch (e) { sysNote("設定沒存成功：" + e.message, true); }
      };
    });
  } catch (e) {
    $("#health-line").textContent = "伺服器連不上：" + e.message;
  }
};

document.querySelectorAll("[data-close]").forEach((b) => {
  b.onclick = () => $("#" + b.dataset.close).classList.add("hidden");
});
document.querySelectorAll(".sheet-mask").forEach((m) => {
  m.addEventListener("click", (e) => { if (e.target === m) m.classList.add("hidden"); });
});
