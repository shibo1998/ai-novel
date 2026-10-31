// 规格08 阶段A 浏览器补验（08-A01 / 08-A02 的可自动判定部分）。
//
// 只用本机 Chrome 的 DevTools Protocol，**不引入任何新依赖**（Node 22 自带 WebSocket）。
// 只连隔离实例（默认 127.0.0.1:8899，临时 AINOVEL_HOME），不碰真实书。
//
// 用法：node scripts/spec08_browser_check.mjs [baseUrl] [bookName] [outDir]
import { spawn } from 'node:child_process'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const BASE = process.argv[2] || 'http://127.0.0.1:8899'
const BOOK = process.argv[3] || '规格08临时书'
const OUT = process.argv[4] || '.pi/baseline'
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const DEBUG_PORT = 9411

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

class CDP {
  constructor(ws) {
    this.ws = ws
    this.seq = 0
    this.pending = new Map()
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data)
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id)
        this.pending.delete(msg.id)
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)
      }
    })
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
    const r = await this.send('Runtime.evaluate', {
      expression, returnByValue: true, awaitPromise: true,
    })
    if (r.exceptionDetails) throw new Error(`页面脚本异常: ${r.exceptionDetails.text}`)
    return r.result.value
  }
}

async function connect() {
  for (let i = 0; i < 60; i += 1) {
    try {
      // 必须连**页面目标**（/json 里的 type=page），浏览器级目标不支持 Page 域
      const res = await fetch(`http://127.0.0.1:${DEBUG_PORT}/json`)
      const targets = await res.json()
      const page = targets.find((t) => t.type === 'page')
      if (!page?.webSocketDebuggerUrl) throw new Error('没有 page 目标')
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

/** 等选择器出现（页面数据是异步拉的，不能只等 load 事件）。 */
async function waitFor(cdp, selector, timeout = 20000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const n = await cdp.eval(`document.querySelectorAll(${JSON.stringify(selector)}).length`)
    if (n > 0) return n
    await sleep(200)
  }
  throw new Error(`等待 ${selector} 超时`)
}

const MEASURE = `(() => {
  const box = document.querySelector('.prose-scroll')
  const first = document.querySelector('.prose-paragraph')
  if (!box || !first) return { ok: false }
  const b = getComputedStyle(box)
  const p = getComputedStyle(first)
  return {
    ok: true,
    fontFamily: b.fontFamily,
    fontSize: b.fontSize,
    lineHeight: b.lineHeight,
    letterSpacing: b.letterSpacing,
    paragraphMarginBottom: p.marginBottom,
    paragraphWhiteSpace: p.whiteSpace,
    textIndent: p.textIndent,
    paragraphCount: document.querySelectorAll('.prose-paragraph').length,
    boxScrollWidth: box.scrollWidth,
    boxClientWidth: box.clientWidth,
    docScrollWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
    viewportHeight: window.innerHeight,
    overflowX: box.scrollWidth - box.clientWidth,
    localStorageIndent: window.localStorage.getItem('proseIndent'),
    localStorageLineNumbers: window.localStorage.getItem('proseLineNumbers'),
  }
})()`

async function toggleIndent(cdp) {
  return cdp.eval(`(() => {
    const boxes = Array.from(document.querySelectorAll('.el-checkbox'))
    const target = boxes.find((el) => (el.textContent || '').includes('首行缩进'))
    if (!target) return 'not-found'
    const input = target.querySelector('input')
    if (!input) return 'no-input'
    input.click()
    return 'clicked'
  })()`)
}

async function main() {
  mkdirSync(OUT, { recursive: true })
  const profile = mkdtempSync(join(tmpdir(), 'ainovel-cdp-'))
  const chrome = spawn(CHROME, [
    '--headless=new',
    `--remote-debugging-port=${DEBUG_PORT}`,
    `--user-data-dir=${profile}`,
    '--window-size=1280,800',
    '--hide-scrollbars=false',
    '--no-first-run',
    '--no-default-browser-check',
    'about:blank',
  ], { stdio: 'ignore' })

  const evidence = { base: BASE, book: BOOK, steps: [], at: new Date().toISOString() }
  let cdp
  try {
    cdp = await connect()
    await cdp.send('Page.enable')
    await cdp.send('Runtime.enable')
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: 1280, height: 800, deviceScaleFactor: 1, mobile: false,
    })

    const url = `${BASE}/#/book/${encodeURIComponent(BOOK)}/chapter/1`
    await cdp.send('Page.navigate', { url })
    await waitFor(cdp, '.prose-paragraph')

    // —— A-T01：计算样式与 3000 字不横向溢出 ——
    const first = await cdp.eval(MEASURE)
    evidence.steps.push({ step: 'A-T01 初始渲染', result: first })

    const shot = await cdp.send('Page.captureScreenshot', { format: 'png' })
    writeFileSync(join(OUT, 'spec08-a01-prose-1280x800.png'), Buffer.from(shot.data, 'base64'))

    // —— A-T01：缩进开关刷新保持 ——
    const clickedOff = await toggleIndent(cdp)
    await sleep(400)
    const afterToggle = await cdp.eval(MEASURE)
    evidence.steps.push({ step: 'A-T01 关闭首行缩进（未刷新）', clicked: clickedOff, result: afterToggle })

    await cdp.send('Page.reload', { ignoreCache: false })
    await sleep(600)
    await waitFor(cdp, '.prose-paragraph')
    const afterReload = await cdp.eval(MEASURE)
    evidence.steps.push({ step: 'A-T01 刷新后保持', result: afterReload })

    const clickedOn = await toggleIndent(cdp)
    await sleep(300)
    await cdp.send('Page.reload', { ignoreCache: false })
    await sleep(600)
    await waitFor(cdp, '.prose-paragraph')
    const restored = await cdp.eval(MEASURE)
    evidence.steps.push({ step: 'A-T01 重新打开并刷新', clicked: clickedOn, result: restored })

    // —— A-T02：原始行号 / 重复引用定位（读 DOM 上映射出的原始行号） ——
    const lineNumbers = await cdp.eval(`(() => {
      const toggle = Array.from(document.querySelectorAll('.el-checkbox'))
        .find((el) => (el.textContent || '').includes('全部原始行号'))
      if (!toggle) return { ok: false, reason: '未找到开关' }
      toggle.querySelector('input')?.click()
      return { ok: true }
    })()`)
    await sleep(500)
    const mapping = await cdp.eval(`(() => {
      const paras = Array.from(document.querySelectorAll('.prose-paragraph'))
      return {
        ok: paras.length > 0,
        count: paras.length,
        spans: paras.map((p) => p.getAttribute('data-start-line')),
        rendered: paras.slice(0, 8).map((p) => (p.textContent || '').slice(0, 24)),
        hasLineNo: document.querySelectorAll('.original-line-no').length,
        emptyLineParagraphs: paras.filter((p) => !(p.textContent || '').trim()).length,
      }
    })()`)
    evidence.steps.push({ step: 'A-T02 行号映射', toggle: lineNumbers, result: mapping })

    const shot2 = await cdp.send('Page.captureScreenshot', { format: 'png' })
    writeFileSync(join(OUT, 'spec08-a02-line-map.png'), Buffer.from(shot2.data, 'base64'))

    writeFileSync(join(OUT, 'spec08-browser-evidence.json'), JSON.stringify(evidence, null, 2))
    console.log(JSON.stringify(evidence, null, 2))
  } finally {
    try { cdp?.ws.close() } catch { /* ignore */ }
    chrome.kill()
  }
}

main().catch((error) => {
  console.error('浏览器补验失败：', error.message)
  process.exit(1)
})
