import type { EngineInterface, PermissionRequestDecision, Register } from 'claude-code'

// 手機聊天室（claude-chat）的授權卡 mod：桌面對話要你按「允許／拒絕」或回答選擇題時，
// 同一題同時送到手機（POST /api/perm），手機按了就把答案交給確認框；桌面先按也行，誰先算誰。
//
// 取代 settings.json 的 PermissionRequest hook（perm-bridge.js），比它多知道一件事：
// 桌面先按時引擎會中止這個 hook（next.signal），mod 當場告訴伺服器「桌面答了」，手機卡片馬上收掉，
// 不用等伺服器翻 jsonl 對帳（那條對帳仍在，當中止沒觸發時的備援）。
//
// 鏈的順序是 [managed 設定 hook, ...mods, 其他設定 hook]：這裡接手就不呼叫 next，perm-bridge.js 不會再開第二張卡；
// 這個 hook 出錯（丟例外、超出預算）時引擎改跑底下的 perm-bridge.js，等於自動退回舊做法。
// 伺服器起的手機 -p 對話（環境有 CLAUDE_CHAT_RUN_ID）不歸這裡：那條由 --settings 的 PreToolUse 處理。
const TOOLS = new Set(['Bash', 'AskUserQuestion'])   // 與 settings.json 原本的 matcher 相同
// 伺服器那邊每輪長輪詢約 20 秒。perm-bridge.js 被設定檔 hook 的 600 秒逾時卡在 9.5 分鐘，
// 手機離開十分鐘卡片就沒了；mod 等 $.http.fetch 不算預算，等多久都行。
// 桌面的確認框沒有時限，這裡也不該有：原本等 55 分鐘，使用者離開超過一小時回來，桌面還在問、手機的卡早被收掉。
// 一天只是給沒人理的確認框一個收尾；伺服器那邊卡片的存活上限（perm.PERM_TTL）要比這裡長。
const WAIT_TOTAL_MS = 24 * 60 * 60 * 1000
// 伺服器重啟（restart-server.cmd 約 3-5 秒）時輪詢會連不上：睡一下再問，卡片重啟後會從 state.sqlite 載回來。
// $.clock.sleep 算在 hook 的 10 秒預算裡（$.http.fetch 不算），所以一張卡總共最多睡這麼久，睡滿就跟以前一樣放棄。
const RETRY_SLEEP_MS = 1000
const RETRY_SLEEP_MAX_MS = 7000

type Json = Record<string, unknown>

async function call($: EngineInterface, base: string, path: string, body?: Json): Promise<Json | null> {
  const r = await $.http.fetch(base + path, body === undefined ? undefined : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) return null
  try {
    return JSON.parse(r.text) as Json
  } catch {
    return null
  }
}

/** 伺服器的答案 → 確認框的決定（與 perm-bridge.js 的 permissionRequest 同一套文字）。 */
export function toDecision(tool: string, input: Json, d: Json): PermissionRequestDecision {
  if (tool === 'AskUserQuestion') {
    // 桌面 app 回答選擇題的方式是在確認框裡把 answers 回填進工具輸入；手機答案走同一條
    if (d.decision !== 'allow') {
      return { behavior: 'deny', message: '使用者在手機上跳過了這個問題，請你自行判斷後繼續，不要再問同一題。' }
    }
    const answers: Record<string, string> = { ...((d.answers as Record<string, string>) || {}) }
    const free = String(d.free_text || '').trim()
    const qs = Array.isArray(input.questions) ? (input.questions as Array<{ question?: string }>) : []
    if (free) for (const q of qs) if (q.question && !answers[q.question]) answers[q.question] = free
    return { behavior: 'allow', updatedInput: { ...input, answers } }
  }
  return d.decision === 'allow'
    ? { behavior: 'allow' }
    : { behavior: 'deny', message: '使用者在手機上拒絕了這個動作。不要重試同一個動作；換個做法或說明原因後停下來。' }
}

export const register: Register = (on) => {
  on('classic.PermissionRequest', async ($, e, next) => {
    if (!TOOLS.has(e.tool_name) || (await $.env.get('CLAUDE_CHAT_RUN_ID'))) return next(e)
    const base = 'http://127.0.0.1:' + ((await $.env.get('CLAUDE_CHAT_PORT')) || '8899')
    const input = (e.tool_input && typeof e.tool_input === 'object' ? e.tool_input : {}) as Json

    let permId: string | undefined
    try {
      const d = await call($, base, '/api/perm', {
        session_id: e.session_id, cwd: e.cwd, tool_name: e.tool_name, tool_input: input,
        reason: '', event: 'PermissionRequest', source: 'mod',   // source：分得出是 mod 還是 perm-bridge.js 開的卡
      })
      permId = d?.perm_id as string | undefined
    } catch {
      // 伺服器沒開：往下交給 perm-bridge.js（它也會連不上、安靜退出），確認框照常
    }
    if (!permId) return next(e)

    // 桌面先按、使用者中斷、上層 hook 先定案 → signal 中止。跟每一輪輪詢賽跑，不必等那輪 20 秒跑完。
    const aborted = new Promise<'aborted'>((resolve) => {
      if (next.signal.aborted) resolve('aborted')
      else next.signal.addEventListener('abort', () => resolve('aborted'), { once: true })
    })
    const deadline = (await $.clock.now()) + WAIT_TOTAL_MS
    let slept = 0
    while ((await $.clock.now()) < deadline) {
      let d: Json | 'aborted' | null
      try {
        d = await Promise.race([call($, base, '/api/perm/' + permId), aborted])
      } catch {
        // 連不上（伺服器正在重啟）：已經開過卡，不往下交（否則 perm-bridge.js 會再開一張）；睡一下再問，睡滿就放棄，確認框照常
        if (slept >= RETRY_SLEEP_MAX_MS) return {}
        slept += RETRY_SLEEP_MS
        try {
          await $.clock.sleep(RETRY_SLEEP_MS, { signal: next.signal })
        } catch {
          // 桌面先按了：下一輪由 aborted 收尾
        }
        continue
      }
      if (d === 'aborted') {
        try {
          await call($, base, '/api/perm/' + permId + '/answer', { decision: 'ask', by: 'desktop' })
        } catch {
          // 伺服器不在：沒人輪詢的卡手機自己會收掉（waiter_gone）
        }
        return {}
      }
      if (!d) return {}                       // 卡片不見了（伺服器重啟又沒載回、或過期）：不留意見，確認框照常
      if (d.decision === 'allow' || d.decision === 'deny') return { decision: toDecision(e.tool_name, input, d) }
      if (d.decision === 'ask') return {}     // 桌面已答（伺服器對帳發現）或卡片被改回桌面自己問
    }
    try {
      await call($, base, '/api/perm/' + permId + '/answer', { decision: 'ask', by: 'timeout' })
    } catch {
      // 同上
    }
    return {}
  })
}
