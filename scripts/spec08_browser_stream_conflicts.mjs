// 规格08 阶段A 浏览器补验之二：A-T03（3000 字 Mock SSE / 上滚不拉回 / 后台恢复）
// 与 A-T07（50 条冲突的筛选、选择、禁用动作）。不引入新依赖：
// 只有 Node 内置 http 做 SSE mock，Node 22 内置 WebSocket 连 Chrome DevTools Protocol。
//
// 用法：node scripts/spec08_browser_stream_conflicts.mjs [baseUrl] [bookName] [outDir]
import { spawn } from 'node:child_process'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const BASE = process.argv[2] || 'http://127.0.0.1:8899'
const BOOK = process.argv[3] || '规格08临时书'
const OUT = process.argv[4] || '.pi/baseline'
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const DEBUG_PORT = 9413
const MOCK_PORT = 8901
const CHARS = 3000
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// —— 构造 3000 字推流文本（可判定的尾字） ——
const SENTENCE = '林寒压住令牌，指腹下的纹路一点点发烫，像骨头里点了盏灯。'
const TEXT = (() => {
  const parts = []
  let n = 0
  while (n < CHARS) {
    const seg = `${SENTENCE}[${String(n + 1).padStart(4, '0')}]`
    parts.push(seg)
    n += seg.length
  }
  return parts.join('\n\n').slice(0, CHARS)
})()
const TAIL = TEXT.slice(-12)

// —— SSE mock：每 20ms 推 2 字，最后发 event: done ——
function startMock() {
  const server = createServer((req, res) => {
    const cors = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Headers': '*',
      'Access-Control-Allow-Methods': 'POST,GET,OPTIONS',
    }
    if (req.method === 'OPTIONS') {
      res.writeHead(204, cors)
      res.end()
      return
    }
    if (!req.url.startsWith('/mock/generate')) {
      res.writeHead(404, cors)
      res.end()
      return
    }
    res.writeHead(200, {
      ...cors,
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    })
    let i = 0
    const timer = setInterval(() => {
      if (i >= TEXT.length) {
        clearInterval(timer)
        res.write(`event: done\ndata: ${JSON.stringify({ ok: true, chars: TEXT.length })}\n\n`)
        res.end()
        return
      }
      const piece = TEXT.slice(i, i + 2)
      i += 2
      res.write(`data: ${JSON.stringify({ text: piece })}\n\n`)
    }, 20)
    req.on('close', () => clearInterval(timer))
  })
  return new Promise((resolve) => server.listen(MOCK_PORT, '127.0.0.1', () => resolve(server)))
}

class CDP {
  constructor(ws) {
    this.ws = ws
    this.seq = 0
    this.pending = new Map()
    this.requests = []
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data)
      if (msg.method === 'Fetch.requestPaused') this.onPaused(msg.params)
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id)
        this.pending.delete(msg.id)
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)
      }
    })
  }

  onPaused(params) {
    const url = params.request.url
    this.requests.push(url)
    if (/\/chapters\/\d+\/generate/.test(url)) {
      this.send('Fetch.continueRequest', {
        requestId: params.requestId,
        url: `http://127.0.0.1:${MOCK_PORT}/mock/generate`,
      }).catch(() => this.send('Fetch.continueRequest', { requestId: params.requestId }).catch(() => {}))
      return
    }
    this.send('Fetch.continueRequest', { requestId: params.requestId }).catch(() => {})
  }

  send(method, params = {}) {
    const id = ++this.seq
    this.ws.send(JSON.stringify({ id, method, params }))
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id)
          reject(new Error(`CDP 超时: ${method}`))
        }
      }, 30000)
    })
  }

  async eval(expression) {
    const r = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
    if (r.exceptionDetails) throw new Error(`页面脚本异常: ${r.exceptionDetails.text}`)
    return r.result.value
  }

  async waitFor(expression, timeout = 25000, label = expression) {
    const deadline = Date.now() + timeout
    while (Date.now() < deadline) {
      if (await this.eval(expression)) return true
      await sleep(150)
    }
    throw new Error(`等待超时: ${label}`)
  }
}

async function connect() {
  for (let i = 0; i < 80; i += 1) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${DEBUG_PORT}/json`)).json()
      const page = targets.find((t) => t.type === 'page')
      if (!page?.webSocketDebuggerUrl) throw new Error('无 page 目标')
      const ws = new WebSocket(page.webSocketDebuggerUrl)
      await new Promise((resolve, reject) => {
        ws.addEventListener('open', resolve, { once: true })
        ws.addEventListener('error', reject, { once: true })
      })
      return new CDP(ws)
    } catch {
      await sleep(250)
    }
  }
  throw new Error('无法连接 Chrome DevTools')
}

const OBSERVE = `(() => {
  window.__obs = { mut: 0, maxLen: 0, last: '', scrolls: [] }
  const attach = () => {
    const box = document.querySelector('.prose-scroll')
    if (!box) return false
    new MutationObserver((recs) => {
      window.__obs.mut += recs.length
      const pre = document.querySelector('.stream-text')
      if (pre) {
        window.__obs.maxLen = Math.max(window.__obs.maxLen, pre.textContent.length)
        window.__obs.last = pre.textContent
      }
    }).observe(box, { subtree: true, childList: true, characterData: true })
    return true
  }
  if (!attach()) return 'no-box'
  return 'ok'
})()`

const SAMPLE = `(() => {
  const box = document.querySelector('.prose-scroll')
  const pre = document.querySelector('.stream-text')
  const back = Array.from(document.querySelectorAll('button')).find((b) => (b.textContent || '').includes('回到最新'))
  const o = window.__obs || { mut: 0, maxLen: 0, last: '' }
  return {
    mut: o.mut,
    maxLen: o.maxLen,
    lastLen: o.last ? o.last.length : 0,
    lastTail: o.last ? o.last.slice(-12) : '',
    scrollTop: box ? Math.round(box.scrollTop) : null,
    scrollHeight: box ? box.scrollHeight : null,
    clientHeight: box ? box.clientHeight : null,
    atBottom: box ? (box.scrollHeight - box.clientHeight - box.scrollTop) <= 48 : null,
    streamBoxPresent: Boolean(pre),
    returnButton: Boolean(back),
  }
})()`

async function scenarioStream(cdp, evidence) {
  const steps = []
  await cdp.send('Page.navigate', { url: `${BASE}/#/book/${encodeURIComponent(BOOK)}/chapter/1` })
  await cdp.waitFor(`document.querySelectorAll('.prose-paragraph').length > 0`, 25000, '正文段落')

  const clicked = await cdp.eval(`(() => {
    const btn = Array.from(document.querySelectorAll('button')).find((b) => (b.textContent || '').includes('开始生成'))
    if (!btn) return 'not-found'
    btn.click()
    return 'clicked'
  })()`)
  steps.push({ step: 'A-T03 点击开始生成', clicked })

  await cdp.waitFor(`Boolean(document.querySelector('.stream-text'))`, 15000, '流式容器')
  await cdp.eval(OBSERVE)

  // 等到正文超过可视区（产生滚动）
  await cdp.waitFor(`(window.__obs?.maxLen || 0) > 700`, 30000, '流式文本 >700 字')
  const beforeScroll = await cdp.eval(SAMPLE)

  // 上滚到顶：距底 > 48px，follow 应关闭
  await cdp.eval(`(() => { const b = document.querySelector('.prose-scroll'); b.scrollTop = 0; b.dispatchEvent(new Event('scroll')); return b.scrollTop })()`)
  await sleep(2500)
  const afterScroll = await cdp.eval(SAMPLE)
  steps.push({ step: 'A-T03 上滚离底 >48px', before: beforeScroll, after: afterScroll })

  // 回到最新：恢复跟随
  await cdp.eval(`(() => {
    const back = Array.from(document.querySelectorAll('button')).find((b) => (b.textContent || '').includes('回到最新'))
    if (back) back.click()
    return Boolean(back)
  })()`)
  await sleep(1500)
  const afterReturn = await cdp.eval(SAMPLE)
  steps.push({ step: 'A-T03 点击回到最新', after: afterReturn })

  // 后台标签：冻结 → 恢复，不应丢尾缓冲
  const beforeFreeze = await cdp.eval(SAMPLE)
  await cdp.send('Page.setWebLifecycleState', { state: 'frozen' }).catch(() => {})
  await sleep(2500)
  await cdp.send('Page.setWebLifecycleState', { state: 'active' }).catch(() => {})
  await sleep(2000)
  const afterResume = await cdp.eval(SAMPLE)
  steps.push({ step: 'A-T03 后台冻结后恢复', before: beforeFreeze, after: afterResume })

  // 等推完：尾字必须完整
  const done = await cdp
    .waitFor(`(window.__obs?.maxLen || 0) >= ${TEXT.length} || !document.querySelector('.stream-text')`, 45000, '推流结束')
    .catch(() => false)
  await sleep(1200)
  const finalState = await cdp.eval(`(() => {
    const o = window.__obs || {}
    return { mut: o.mut, maxLen: o.maxLen, lastLen: o.last ? o.last.length : 0, lastTail: o.last ? o.last.slice(-12) : '' }
  })()`)
  steps.push({ step: 'A-T03 推流结束', done, final: finalState, expectedTail: TAIL, expectedChars: TEXT.length })

  const shot = await cdp.send('Page.captureScreenshot', { format: 'png' })
  writeFileSync(join(OUT, 'spec08-a03-stream.png'), Buffer.from(shot.data, 'base64'))
  evidence['A-T03'] = { steps, expect: { chars: TEXT.length, tail: TAIL } }
  return steps
}

async function pickSelectOption(cdp, index, optionText) {
  const opened = await cdp.eval(`(() => {
    const selects = Array.from(document.querySelectorAll('.el-select'))
    const target = selects[${index}]
    if (!target) return 'no-select'
    const input = target.querySelector('.el-select__wrapper') || target
    input.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    return 'opened'
  })()`)
  await sleep(700)
  const options = await cdp.eval(`Array.from(document.querySelectorAll('.el-select-dropdown__item')).map((el) => (el.textContent || '').trim())`)
  const picked = await cdp.eval(`(() => {
    const items = Array.from(document.querySelectorAll('.el-select-dropdown__item'))
    const hit = items.find((el) => (el.textContent || '').includes(${JSON.stringify(optionText)}))
    if (!hit) return 'no-option'
    hit.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    return 'picked'
  })()`)
  return { opened, options, picked }
}

const VISIBLE = `(() => {
  const box = Array.from(document.querySelectorAll('.el-checkbox'))
    .find((el) => (el.textContent || '').includes('选择当前筛选可见项'))
  return box ? (box.textContent || '').replace(/\\s+/g, '') : null
})()`

const BUTTON_STATE = `(() => {
  const find = (t) => Array.from(document.querySelectorAll('button')).find((b) => (b.textContent || '').includes(t))
  const adopt = find('批量采纳候选')
  const keep = find('批量保留档案')
  return {
    adoptDisabled: adopt ? adopt.disabled : null,
    keepDisabled: keep ? keep.disabled : null,
    renderedItems: document.querySelectorAll('.el-tag--danger').length,
    openManualForms: document.querySelectorAll('.el-form').length,
    messages: Array.from(document.querySelectorAll('.el-message')).map((m) => (m.textContent || '').trim()),
  }
})()`

async function scenarioConflicts(cdp, evidence) {
  const steps = []
  const before = cdp.requests.length
  await cdp.send('Page.navigate', { url: `${BASE}/#/conflicts` })
  await cdp.waitFor(`document.querySelectorAll('.el-tag--danger').length >= 50`, 25000, '50 条冲突')
  await sleep(500)

  steps.push({ step: 'A-T07 初始（默认折叠）', selectedLabel: await cdp.eval(VISIBLE), state: await cdp.eval(BUTTON_STATE) })

  // 默认折叠：展开一条后应出现详情表单
  await cdp.eval(`(() => {
    const btn = Array.from(document.querySelectorAll('button')).find((b) => (b.textContent || '').includes('展开详情'))
    if (btn) btn.click()
    return Boolean(btn)
  })()`)
  await sleep(500)
  steps.push({ step: 'A-T07 展开一条详情', state: await cdp.eval(BUTTON_STATE) })
  await cdp.eval(`(() => {
    const btn = Array.from(document.querySelectorAll('button')).find((b) => (b.textContent || '').includes('收起详情'))
    if (btn) btn.click()
  })()`)
  await sleep(300)

  const bookFilter = await pickSelectOption(cdp, 0, '规格08假书B')
  await sleep(700)
  steps.push({ step: 'A-T07 按书筛选', pick: bookFilter, selectedLabel: await cdp.eval(VISIBLE), state: await cdp.eval(BUTTON_STATE) })

  const kindFilter = await pickSelectOption(cdp, 1, '角色字段待核对')
  await sleep(700)
  steps.push({ step: 'A-T07 书+类型筛选', pick: kindFilter, selectedLabel: await cdp.eval(VISIBLE), state: await cdp.eval(BUTTON_STATE) })

  const selectAll = `(() => {
    const box = Array.from(document.querySelectorAll('.el-checkbox')).find((el) => (el.textContent || '').includes('选择当前筛选可见项'))
    if (!box) return 'not-found'
    box.querySelector('input')?.click()
    return 'clicked'
  })()`
  await cdp.eval(selectAll)
  await sleep(400)
  steps.push({ step: 'A-T07 全选可见项', selectedLabel: await cdp.eval(VISIBLE), state: await cdp.eval(BUTTON_STATE) })

  // 仅说明/未知字段项不得自动批量关闭：点批量采纳应只给警告、零 resolve 请求
  const requestsBeforeBatch = cdp.requests.filter((u) => u.includes('/resolve')).length
  await cdp.eval(`(() => {
    const btn = Array.from(document.querySelectorAll('button')).find((b) => (b.textContent || '').includes('批量采纳候选'))
    if (btn) btn.click()
    return Boolean(btn)
  })()`)
  await sleep(1200)
  const requestsAfterBatch = cdp.requests.filter((u) => u.includes('/resolve')).length
  steps.push({
    step: 'A-T07 未知/仅说明项批量采纳',
    resolveRequests: requestsAfterBatch - requestsBeforeBatch,
    selectedLabel: await cdp.eval(VISIBLE),
    state: await cdp.eval(BUTTON_STATE),
  })

  await cdp.eval(selectAll)
  await sleep(400)
  steps.push({ step: 'A-T07 清空选择', selectedLabel: await cdp.eval(VISIBLE), state: await cdp.eval(BUTTON_STATE) })

  evidence['A-T07'] = {
    steps,
    resolveRequestsDuringFilter: cdp.requests.slice(before).filter((u) => u.includes('/resolve')).length,
  }
  return steps
}

async function main() {
  mkdirSync(OUT, { recursive: true })
  const mock = await startMock()
  const profile = mkdtempSync(join(tmpdir(), 'ainovel-cdp2-'))
  const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${DEBUG_PORT}`,
    `--user-data-dir=${profile}`, '--window-size=1280,800', '--no-first-run',
    '--no-default-browser-check', 'about:blank'], { stdio: 'ignore' })

  let cdp
  const evidence = { base: BASE, book: BOOK, at: new Date().toISOString() }
  try {
    cdp = await connect()
    await cdp.send('Page.enable')
    await cdp.send('Runtime.enable')
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false })
    await cdp.send('Fetch.enable', {
      patterns: [{ urlPattern: '*/chapters/*/generate*', requestStage: 'Request' }],
    })

    await scenarioStream(cdp, evidence)
    await cdp.send('Fetch.disable')
    await scenarioConflicts(cdp, evidence)

    writeFileSync(join(OUT, 'spec08-a03-a07-evidence.json'), JSON.stringify(evidence, null, 2))
    console.log(JSON.stringify(evidence, null, 2))
  } finally {
    try { cdp?.ws.close() } catch { /* ignore */ }
    chrome.kill()
    mock.close()
  }
}

main().catch((error) => {
  console.error('补验失败：', error.message)
  process.exit(1)
})
