import { expect, test } from 'claude-code/testing'
import type { Engine, On } from 'claude-code/testing'

type Req = { url: string; body: Record<string, unknown> | null }

// 引擎底下的世界：伺服器照 replies 依序回答輪詢；收到的請求都記在 reqs。bottom 記錄有沒有往下交給設定檔 hook。
// fail(url, i)：第 i 個請求（從 0 數）要不要連不上。sleeps 記錄 mod 睡了幾次、各多久。
function world(on: On, opts: { polls?: unknown[]; open?: unknown; env?: Record<string, string>; fail?: (url: string, i: number) => boolean } = {}) {
  const reqs: Req[] = []
  const polls = [...(opts.polls ?? [])]
  const state = { bottom: 0, now: 0, sleeps: [] as number[] }
  on('http.fetch', (_$, e) => {
    const { url, init } = e as { url: string; init?: { body?: string } }
    reqs.push({ url, body: init?.body ? JSON.parse(init.body) : null })
    if (opts.fail?.(url, reqs.length - 1)) throw new Error('ECONNREFUSED')
    let reply: unknown = { ok: true }
    if (url.endsWith('/api/perm')) reply = opts.open ?? { perm_id: 'p1' }
    else if (/\/api\/perm\/p1$/.test(url)) reply = polls.length ? polls.shift() : { pending: true }
    return { value: { status: 200, ok: true, headers: {}, text: JSON.stringify(reply) } } as never
  })
  on('env.get', (_$, e) => ({ value: (opts.env ?? {})[(e as { name: string }).name] }) as never)
  // 每問一次時間就過 1 分鐘：測逾時用，其他測試在 9 輪內就有答案
  on('clock.now', () => ({ value: (state.now += 60_000) }) as never)
  on('clock.sleep', (_$, e) => {
    state.sleeps.push((e as { ms: number }).ms)
    return { value: undefined } as never
  })
  on('classic.PermissionRequest', () => {
    state.bottom++
    return {}
  })
  return { reqs, state }
}

const ask = ($: Engine, tool_name: string, tool_input: Record<string, unknown>) =>
  $.classic.PermissionRequest({ tool_name, tool_input, session_id: 'sid-1', cwd: 'C:/p' } as never)

test('Bash：開卡帶 session_id 與 event，輪詢到允許 → allow，不往下交', async ($, on) => {
  const w = world(on, { polls: [{ pending: true }, { decision: 'allow' }] })
  const r = await ask($, 'Bash', { command: 'git push' })
  expect(r.decision).toEqual({ behavior: 'allow' })
  expect(w.reqs[0].url).toBe('http://127.0.0.1:8899/api/perm')
  expect(w.reqs[0].body).toEqual(expect.objectContaining({ session_id: 'sid-1', tool_name: 'Bash', event: 'PermissionRequest' }))
  expect(w.reqs.filter((q) => q.url.endsWith('/api/perm/p1')).length).toBe(2)
  expect(w.state.bottom).toBe(0)
})

test('Bash：手機拒絕 → deny 帶給模型的說明', async ($, on) => {
  world(on, { polls: [{ decision: 'deny' }] })
  const r = await ask($, 'Bash', { command: 'rm -rf x' })
  expect(r.decision?.behavior).toBe('deny')
  expect(String((r.decision as { message?: string }).message)).toContain('拒絕')
})

test('選擇題：手機答案回填 answers，打字作答補沒選的題', async ($, on) => {
  world(on, { polls: [{ decision: 'allow', answers: { 'Q1?': 'A' }, free_text: '自己寫的' }] })
  const input = { questions: [{ question: 'Q1?' }, { question: 'Q2?' }] }
  const r = await ask($, 'AskUserQuestion', input)
  expect(r.decision).toEqual({ behavior: 'allow', updatedInput: { ...input, answers: { 'Q1?': 'A', 'Q2?': '自己寫的' } } })
})

test('伺服器對帳發現桌面已答（decision ask）→ 不留意見、不往下交', async ($, on) => {
  const w = world(on, { polls: [{ decision: 'ask', by: 'desktop' }] })
  const r = await ask($, 'Bash', { command: 'ls' })
  expect(r.decision).toBeUndefined()
  expect(w.state.bottom).toBe(0)
})

test('不是 Bash／選擇題、或手機起的 -p 對話 → 直接往下交，不碰伺服器', async ($, on) => {
  const w = world(on, { env: {} })
  await ask($, 'Edit', { file_path: 'a' })
  expect(w.state.bottom).toBe(1)
  expect(w.reqs.length).toBe(0)
})

test('手機起的 -p 對話（有 CLAUDE_CHAT_RUN_ID）不歸這裡', async ($, on) => {
  const w = world(on, { env: { CLAUDE_CHAT_RUN_ID: 'r1' } })
  await ask($, 'Bash', { command: 'ls' })
  expect(w.state.bottom).toBe(1)
  expect(w.reqs.length).toBe(0)
})

test('CLAUDE_CHAT_PORT 改埠', async ($, on) => {
  const w = world(on, { env: { CLAUDE_CHAT_PORT: '9911' }, polls: [{ decision: 'allow' }] })
  await ask($, 'Bash', { command: 'ls' })
  expect(w.reqs[0].url).toBe('http://127.0.0.1:9911/api/perm')
})

test('伺服器沒開（開卡就失敗）→ 往下交給設定檔 hook', async ($, on) => {
  const w = world(on, { fail: () => true })
  const r = await ask($, 'Bash', { command: 'ls' })
  expect(r.decision).toBeUndefined()
  expect(w.state.bottom).toBe(1)
})

test('開卡後一直連不上 → 睡滿 7 秒就放棄：不留意見，也不往下交（免得開第二張卡）', async ($, on) => {
  const w = world(on, { fail: (url) => url.endsWith('/api/perm/p1') })
  const r = await ask($, 'Bash', { command: 'ls' })
  expect(r.decision).toBeUndefined()
  expect(w.state.bottom).toBe(0)
  expect(w.state.sleeps).toEqual([1000, 1000, 1000, 1000, 1000, 1000, 1000])
  expect(w.reqs.some((q) => q.url.endsWith('/answer'))).toBe(false)
})

test('輪詢中途伺服器重啟（連不上幾秒）→ 睡一下再問，接得回來', async ($, on) => {
  // 第 0 個請求是開卡；第 1、2 個輪詢連不上（重啟中），第 3 個就有答案
  const w = world(on, { fail: (url, i) => url.endsWith('/api/perm/p1') && i <= 2, polls: [{ decision: 'allow' }] })
  const r = await ask($, 'Bash', { command: 'ls' })
  expect(r.decision).toEqual({ behavior: 'allow' })
  expect(w.state.sleeps).toEqual([1000, 1000])
  expect(w.state.bottom).toBe(0)
})

test('超過一小時沒人按還在等：第 70 分鐘才按允許 → allow，沒有回報 timeout', async ($, on) => {
  const w = world(on, { polls: [...Array(69).fill({ pending: true }), { decision: 'allow' }] })
  const r = await ask($, 'Bash', { command: 'ls' })
  expect(r.decision).toEqual({ behavior: 'allow' })
  expect(w.reqs.filter((q) => q.url.endsWith('/api/perm/p1')).length).toBe(70)
  expect(w.reqs.some((q) => q.url.endsWith('/answer'))).toBe(false)
})

test('24 小時沒人按 → 回報 timeout、不留意見', async ($, on) => {
  const w = world(on)
  const r = await ask($, 'Bash', { command: 'ls' })
  expect(r.decision).toBeUndefined()
  const last = w.reqs[w.reqs.length - 1]
  expect(last.url).toBe('http://127.0.0.1:8899/api/perm/p1/answer')
  expect(last.body).toEqual({ decision: 'ask', by: 'timeout' })
  // 算 deadline 那次 now() 也吃掉一分鐘，所以是 1439 輪
  expect(w.reqs.filter((q) => q.url.endsWith('/api/perm/p1')).length).toBe(24 * 60 - 1)
  expect(w.state.bottom).toBe(0)
})
