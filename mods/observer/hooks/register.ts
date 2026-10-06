import type { EngineInterface, Register } from 'claude-code'

// 手機聊天室（claude-chat）的觀測 mod。掛在每個 Claude Code 對話裡（桌面 app 與伺服器起的 -p 都會載），
// 只做三件回報、不改任何行為：
//   turn.start / turn.complete → 伺服器知道這個對話「確認執行中」（比看 jsonl 有沒有在動準）
//   session.receive（手機直送的訊息抵達）→ 伺服器把等了 6 秒的回條標成「已送達」
// 伺服器沒開、埠不對、任何錯誤：安靜略過，對話照常。
// ponytail: 埠寫死 8899（與 claude_chat/config.py 的 PORT 一致）；要改成可設定再讀 options.userConfig。
const OBSERVE_URL = 'http://127.0.0.1:8899/api/observe'

async function report($: EngineInterface, body: Record<string, unknown>): Promise<void> {
  try {
    await $.http.fetch(OBSERVE_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // sid 每次現讀（async）：/clear 之後行程換了 session id，但不會再 fire session.start
      body: JSON.stringify({ sid: await $.session.id(), ts: Date.now() / 1000, ...body }),
    })
  } catch {
    // 伺服器不在：觀測失敗不能影響對話
  }
}

export const register: Register = (on) => {
  on('turn.start', async ($, e, next) => {
    await report($, { kind: 'turn.start', turnId: e.turnId })
    return next(e)
  })
  on('turn.complete', async ($, e, next) => {
    await report($, { kind: 'turn.complete', turnId: e.turnId, reason: e.reason })
    return next(e)
  })
  on('session.receive', async ($, e, next) => {
    // 只認手機聊天室直送的（伺服器登記的 peer 名字叫 phone）；別的 session 的訊息不關我們的事。
    // 型別裡 peer 類有兩個 kind（'peer'＝另一個對話的模型、'peer-send-message'＝走 prompt 那條），
    // 手機那封實際落在哪個沒有文件說，兩個都收，真正的判別是 from-name。
    const k = e.origin.kind
    if ((k === 'peer' || k === 'peer-send-message') && /from-name="phone"/.test(e.text)) {
      await report($, { kind: 'receive' })
    }
    return next(e)
  })
}
