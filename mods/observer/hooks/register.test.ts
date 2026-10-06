import { expect, test } from 'claude-code/testing'
import type { Engine, On } from 'claude-code/testing'

// 引擎底下的世界：伺服器收到的回報都記在 posts；session id 固定
function world(on: On) {
  const posts: Array<Record<string, unknown>> = []
  on('http.fetch', (_$, e) => {
    const init = (e as { init?: { body?: string } }).init
    posts.push(JSON.parse(init?.body ?? '{}'))
    return { value: { status: 200, ok: true, headers: {}, text: '{}' } } as never
  })
  on('session.id', () => ({ value: 'sid-1' }) as never)
  on('session.start', () => ({ cwd: 'C:/p' }) as never)
  on('turn.start', (_$, e) => ({ turnId: e.turnId }) as never)
  on('turn.complete', () => ({ text: '' }) as never)
  on('session.receive', (_$, e) => ({ text: e.text }) as never)
  return posts
}

const start = ($: Engine) => $.session.start({ cwd: 'C:/p', surface: 'terminal', isInteractive: true } as never)

test('回合開始與結束各回報一次，帶對話 id 與 turnId', async ($, on) => {
  const posts = world(on)
  await start($)
  await $.turn.start({ text: 'hi', turnId: 't1' } as never)
  await $.turn.complete({ reason: 'answer', turnId: 't1', answer: 'ok', durationMs: 1, isAborted: false } as never)
  expect(posts.map((p) => p.kind)).toEqual(['turn.start', 'turn.complete'])
  expect(posts[0].turnId).toBe('t1')
  expect(posts[0].sid).toBe('sid-1')
  expect(posts[1].reason).toBe('answer')
})

test('只有手機直送的訊息才回報抵達', async ($, on) => {
  const posts = world(on)
  await start($)
  const wrap = (name: string) => `<cross-session-message from="uds:x" from-name="${name}" from-mode="bypass">\nhi\n</cross-session-message>`
  await $.session.receive({ origin: { kind: 'peer' }, text: wrap('phone') } as never)
  await $.session.receive({ origin: { kind: 'peer-send-message' }, text: wrap('phone') } as never)
  await $.session.receive({ origin: { kind: 'peer' }, text: wrap('other-session') } as never)
  await $.session.receive({ origin: { kind: 'bridge' }, text: wrap('phone') } as never)
  expect(posts.map((p) => p.kind)).toEqual(['receive', 'receive'])
})

test('伺服器沒開：回報失敗，回合照常進行', async ($, on) => {
  on('http.fetch', () => { throw new Error('ECONNREFUSED') })
  on('session.id', () => ({ value: 'sid-1' }) as never)
  on('session.start', () => ({ cwd: 'C:/p' }) as never)
  let reached = false
  on('turn.start', (_$, e) => { reached = true; return { turnId: e.turnId } as never })
  await start($)
  await $.turn.start({ text: 'hi', turnId: 't1' } as never)
  expect(reached).toBe(true)
})
