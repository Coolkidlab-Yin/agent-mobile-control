/* claude-chat 前端 — cards：選項卡與授權卡（AskUserQuestion、先問我模式、桌面確認框）
   拆自 app.js；瀏覽器拿到的 /static/app.js 是伺服器把 static/js/ 依序接起來的（順序見 api.py 的 JS_PARTS）。 */
"use strict";

/* ---------- AskUserQuestion 選項卡 ---------- */
function renderAskCard(it) {
  const card = buildAskCard(it.questions, "🙋 它想問你", (answers, freeText, skipped) => {
    api("/api/ask/" + it.ask_id + "/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers: answers || {}, free_text: freeText || "", skipped: !!skipped }),
    }).catch(() => {});
    lockAskCard(it.ask_id, skipped ? "（已跳過）" : null);
  });
  card.dataset.askId = it.ask_id;
  msgsEl.appendChild(card);
}

/* 選項卡本體：questions 依 AskUserQuestion 格式；submit(answers, freeText, skipped) */
function buildAskCard(questions, head, submit) {
  const card = document.createElement("div");
  card.className = "msg ai ask-card";
  const picked = {};   // question -> Set(labels)

  let html = '<div class="ask-head">' + head + "</div>";
  for (const q of questions || []) {
    const multi = !!q.multiSelect;
    html += '<div class="ask-q" data-q="' + esc(q.question) + '" data-multi="' + (multi ? 1 : 0) + '">';
    if (q.header) html += '<div class="ask-tag">' + esc(q.header) + "</div>";
    html += '<div class="ask-question">' + esc(q.question) + "</div>";
    for (const o of q.options || []) {
      html += '<button class="ask-opt" data-label="' + esc(o.label) + '">' +
        '<b>' + esc(o.label) + "</b>" +
        (o.description ? "<span>" + esc(o.description) + "</span>" : "") +
        "</button>";
    }
    if (multi) html += '<button class="ask-multi-ok">就選這些</button>';
    html += "</div>";
  }
  html += '<div class="ask-free"><input type="text" placeholder="或用打字回答…">' +
    '<button class="ask-send">送出</button></div>' +
    '<button class="ask-skip">跳過，讓它自己決定</button>';
  card.innerHTML = html;

  card.querySelectorAll(".ask-q").forEach((qEl) => {
    const qText = qEl.dataset.q;
    const multi = qEl.dataset.multi === "1";
    qEl.querySelectorAll(".ask-opt").forEach((btn) => {
      btn.onclick = () => {
        const label = btn.dataset.label;
        if (multi) {
          const set = picked[qText] = picked[qText] || new Set();
          if (set.has(label)) { set.delete(label); btn.classList.remove("on"); }
          else { set.add(label); btn.classList.add("on"); }
          return;
        }
        picked[qText] = new Set([label]);
        btn.classList.add("on");
        // 單選題全部答完才送出
        const allQ = [...card.querySelectorAll(".ask-q")];
        if (allQ.every((x) => picked[x.dataset.q] && picked[x.dataset.q].size)) {
          const answers = {};
          for (const [k, v] of Object.entries(picked)) answers[k] = [...v].join("、");
          submit(answers, "", false);
        }
      };
    });
  });
  card.querySelectorAll(".ask-multi-ok").forEach((btn) => {
    btn.onclick = () => {
      const answers = {};
      for (const [k, v] of Object.entries(picked)) answers[k] = [...v].join("、");
      submit(answers, "", false);
    };
  });
  card.querySelector(".ask-send").onclick = () => {
    const v = card.querySelector(".ask-free input").value.trim();
    if (!v) return;
    const answers = {};
    for (const [k, s] of Object.entries(picked)) answers[k] = [...s].join("、");
    submit(answers, v, false);
  };
  card.querySelector(".ask-skip").onclick = () => submit({}, "", true);
  return card;
}

function lockAskCard(askId, note) {
  const card = msgsEl.querySelector('.ask-card[data-ask-id="' + askId + '"]');
  if (!card || card.classList.contains("done")) return;
  card.classList.add("done");
  card.querySelectorAll("button, input").forEach((el) => { el.disabled = true; });
  if (note) {
    const n = document.createElement("div");
    n.className = "ask-note";
    n.textContent = note;
    card.appendChild(n);
  }
}

/* ---------- 逐項授權卡（先問我模式／危險指令，桌面 session 的也會出現在這） ---------- */
function permNote(decision, by, tool) {
  if (tool === "AskUserQuestion") {
    if (by === "desktop") return "已在桌面回答了";
    if (by === "timeout" || decision === "ask") return "太久沒人答，改由桌面 App 自己問";
    return decision === "deny" ? "（已跳過）" : "已回答";
  }
  const who = by === "desktop" ? "已在桌面按了：" : by === "timeout" ? "沒人按，改由桌面 App 自己問：" : "";
  if (decision === "allow") return who + "已允許";
  if (decision === "deny") return who + "已拒絕";
  if (decision === "ask") return "太久沒人按，改由桌面 App 的確認框處理";
  return who || null;
}

function renderPermCard(it) {
  if (it.tool === "AskUserQuestion" && it.questions) {
    // 桌面正在跑的對話問選擇題：桌面 App 也跳了同一題，先答的算數；答案走授權通道回填
    const qc = buildAskCard(it.questions, "🙋 桌面的對話想問你（桌面也跳了同一題，先答的算數）", (answers, freeText, skipped) => {
      api("/api/perm/" + it.perm_id + "/answer", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision: skipped ? "deny" : "allow", answers: answers || {}, free_text: freeText || "" }),
      }).catch(() => {});
      lockPermCard(it.perm_id, skipped ? "（已跳過）" : "已回答");
    });
    qc.classList.add("perm-card");
    qc.dataset.permId = it.perm_id;
    msgsEl.appendChild(qc);
    return;
  }
  const card = document.createElement("div");
  card.className = "msg ai ask-card perm-card";
  card.dataset.permId = it.perm_id;
  const head = it.reason
    ? (it.source === "desktop" ? "⚠️ 桌面正在跑的對話碰到危險指令，要放行嗎？（桌面也跳了同一個框，先按的算數）" : "⚠️ 危險指令，要放行嗎？")
    : "🔐 它想做這件事，可以嗎？";
  card.innerHTML =
    '<div class="ask-head">' + head + "</div>" +
    (it.reason ? '<div class="perm-reason">' + esc(it.reason) + "</div>" : "") +
    '<div class="ask-tag">' + esc(it.tool || "工具") + "</div>" +
    (it.detail ? '<div class="ask-question">' + esc(it.detail) + "</div>" : "") +
    (it.preview && it.preview !== it.detail ? '<pre class="perm-preview">' + esc(it.preview) + "</pre>" : "") +
    '<div class="perm-btns">' +
      '<button class="ask-opt perm-allow"><b>允許</b></button>' +
      '<button class="ask-opt perm-deny"><b>拒絕</b></button>' +
    "</div>" +
    (it.source === "desktop" ? "" : '<button class="ask-skip perm-all">這次工作剩下的全部允許（不再問）</button>');
  const submit = (decision) => {
    api("/api/perm/" + it.perm_id + "/answer", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    }).catch(() => {});
    lockPermCard(it.perm_id, decision === "deny" ? "已拒絕" : decision === "allow_all" ? "已允許（之後不再問）" : "已允許");
  };
  card.querySelector(".perm-allow").onclick = () => submit("allow");
  card.querySelector(".perm-deny").onclick = () => submit("deny");
  const allBtn = card.querySelector(".perm-all");
  if (allBtn) allBtn.onclick = () => submit("allow_all");
  msgsEl.appendChild(card);
}

function lockPermCard(permId, note) {
  const card = msgsEl.querySelector('.perm-card[data-perm-id="' + permId + '"]');
  if (!card || card.classList.contains("done")) return;
  card.classList.add("done");
  card.querySelectorAll("button, input").forEach((el) => { el.disabled = true; });
  if (note) {
    const n = document.createElement("div");
    n.className = "ask-note";
    n.textContent = note;
    card.appendChild(n);
  }
}
