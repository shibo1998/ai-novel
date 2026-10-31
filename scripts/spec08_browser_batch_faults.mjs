// 规格08 阶段A 浏览器补验之三：A-T08 批量裁决的三次故障场景
// （取消确认零请求 / 第 17 条明确失败 / 第 18 条结果未知 / 重试不重复成功项）。
// 只在隔离实例上跑，会真实写入该临时 home 的档案；不引入新依赖。
//
// 用法：node scripts/spec08_browser_batch_faults.mjs [baseUrl] [bookName] [outDir]
import { spawn } from 'node:child_process'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const BASE = process.argv[2] || 'http://127.0.0.1:8899'
const BOOK = process.argv[3] || '规格08假书B'
const OUT = process.argv[4] || '.pi/baseline'
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const DEBUG_PORT = 9415
const FAULT_AT = 17
const NETWORK_AT = 18
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

class CDP {
  constructor(ws) {
    this.ws = ws
    this.seq = 0
    this.pending = new Map()
    this.resolveLog = []
    this.faults = true
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
    if (!/\/conflicts\/\d+\/resolve/.test(url)) {
      this.send('Fetch.continueRequest', { requestId: params.requestId }).catch(() => {})
      return
    }
    this.resolveLog.push(url)
    const n = this.resolveLog.length
    if (this.faults && n === FAULT_AT) {
      // 明确失败：服务端 500（前端应记 failed 并停止后续项）
      const body = Buffer.from(JSON.stringify({ detail: { code: 'CONFLICT_WRITE_FAILED', message: '磁盘不可写（注入故障）' } })).toString('base64')
      this.send('Fetch.fulfillRequest', {
        requestId: params.requestId,
        responseCode: 500,
        responseHeaders: [
          { name: 'Content-Type', value: 'application/json' },
          { name: 'Access-Control-Allow-Origin', value: '*' },
        ],
        body,
      }).catch(() => {})
      return
    }
    if (this.faults && n === NETWORK_AT) {
      // 结果未知：直接断连接，前端应记 unknown 并要求先核验
      this.send('Fetch.failRequest', { requestId: params.requestId, errorReason: 'ConnectionReset' }).catch(() => {})
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

  async waitFor(expression, timeout = 30000, label = expression) {
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

async function pickSelectOption(cdp, index, optionText) {
  await cdp.eval(`(() => {
    const target = Array.from(document.querySelectorAll('.el-select'))[${index}]
    const input = target?.querySelector('.el-select__wrapper') || target
    input?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    return Boolean(target)
  })()`)
  await sleep(700)
  return cdp.eval(`(() => {
    const hit = Array.from(document.querySelectorAll('.el-select-dropdown__item'))
      .find((el) => (el.textContent || '').includes(${JSON.stringify(optionText)}))
    if (!hit) return 'no-option'
    hit.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    return 'picked'
  })()`)
}

const RESULT_BADGES = `(() => {
  const tags = Array.from(document.querySelectorAll('.el-tag--success, .el-tag--warning, .el-tag--info'))
    .map((t) => (t.textContent || '').trim())
    .filter((t) => ['成功', '失败', '待确认', '未执行'].some((k) => t.includes(k)))
  const counts = {}
  for (const t of tags) counts[t] = (counts[t] || 0) + 1
  return counts
})()`

const MESSAGES = `Array.from(document.querySelectorAll('.el-message')).map((m) => (m.textContent || '').trim())`

const CONFIRM = `(() => {
  const box = document.querySelector('.el-message-box')
  if (!box) return 'no-box'
  const buttons = Array.from(box.querySelectorAll('button'))
  const ok = buttons.find((b) => (b.textContent || '').includes('确定') || (b.textContent || '').includes('确认'))
  if (!ok) return 'no-ok'
  ok.click()
  return 'confirmed'
})()`

const CANCEL = `(() => {
  const box = document.querySelector('.el-message-box')
  if (!box) return 'no-box'
  const buttons = Array.from(box.querySelectorAll('button'))
  const no = buttons.find((b) => (b.textContent || '').includes('取消'))
  if (!no) return 'no-cancel:' + buttons.map((b) => (b.textContent || '').trim()).join('|')
  no.click()
  return 'cancelled'
})()`

/** 等对话框出现 → 记录按钮文案 → 点指定按钮。
 *  注意：本项目未配置 element-plus 中文 locale，MessageBox 按钮实测是 Cancel/OK，
 *  因此按 class 点（primary = 确认，其余 = 取消），不靠文案。 */
async function dialogAction(cdp, want) {
  const appeared = await cdp
    .waitFor(`Boolean(document.querySelector('.el-message-box'))`, 8000, '裁决确认框')
    .catch(() => false)
  if (!appeared) return { appeared: false, buttons: [] }
  const buttons = await cdp.eval(`Array.from(document.querySelectorAll('.el-message-box button')).map((b) => (b.textContent || '').trim())`)
  const clicked = await cdp.eval(`(() => {
    const box = document.querySelector('.el-message-box')
    if (!box) return 'no-box'
    const btns = Array.from(box.querySelectorAll('.el-message-box__btns button'))
    const primary = btns.find((b) => b.classList.contains('el-button--primary'))
    const secondary = btns.find((b) => !b.classList.contains('el-button--primary'))
    const hit = ${JSON.stringify(want)} === '确定' ? primary : secondary
    if (!hit) return 'no-button'
    hit.click()
    return 'clicked'
  })()`)
  return { appeared, buttons, clicked }
}

async function termCounts(book) {
  const url = `${BASE}/api/books/${encodeURIComponent(book)}/bible`
  const data = await (await fetch(url)).json()
  const table = String(data['术语表'] || '')
  const out = {}
  for (let i = 1; i <= 20; i += 1) {
    const term = `批量术法${String(i).padStart(2, '0')}`
    out[term] = table.split(`| ${term} |`).length - 1
  }
  return out
}

async function main() {
  mkdirSync(OUT, { recursive: true })
  const profile = mkdtempSync(join(tmpdir(), 'ainovel-cdp3-'))
  const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${DEBUG_PORT}`,
    `--user-data-dir=${profile}`, '--window-size=1280,800', '--no-first-run',
    '--no-default-browser-check', 'about:blank'], { stdio: 'ignore' })

  const evidence = { base: BASE, book: BOOK, at: new Date().toISOString(), faultAt: FAULT_AT, networkAt: NETWORK_AT, steps: [] }
  let cdp
  try {
    cdp = await connect()
    await cdp.send('Page.enable')
    await cdp.send('Runtime.enable')
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false })
    await cdp.send('Fetch.enable', { patterns: [{ urlPattern: '*/conflicts/*/resolve*', requestStage: 'Request' }] })

    await cdp.send('Page.navigate', { url: `${BASE}/#/conflicts` })
    await cdp.waitFor(`document.querySelectorAll('.el-tag--danger').length > 0`, 25000, '冲突列表')
    await sleep(600)
    const pickBook = await pickSelectOption(cdp, 0, BOOK)
    await sleep(900)
    const afterBook = await cdp.eval(`document.querySelectorAll('.el-tag--danger').length`)
    await cdp.eval(`document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))`)
    await sleep(400)
    const pickKind = await pickSelectOption(cdp, 1, '新设定待补录')
    await sleep(900)
    const afterKind = await cdp.eval(`document.querySelectorAll('.el-tag--danger').length`)
    evidence.filters = { pickBook, afterBook, pickKind, afterKind }
    const visible = await cdp.eval(`(() => {
      const box = Array.from(document.querySelectorAll('.el-checkbox')).find((el) => (el.textContent || '').includes('选择当前筛选可见项'))
      return box ? (box.textContent || '').replace(/\\s+/g, '') : null
    })()`)

    await cdp.eval(`(() => {
      const box = Array.from(document.querySelectorAll('.el-checkbox')).find((el) => (el.textContent || '').includes('选择当前筛选可见项'))
      box?.querySelector('input')?.click()
    })()`)
    await sleep(400)

    const beforeCancel = cdp.resolveLog.length
    // —— 场景一：取消批量二次确认 → 零 resolve 请求 ——
    await cdp.eval(`Array.from(document.querySelectorAll('button')).find((b) => (b.textContent || '').includes('批量采纳候选'))?.click()`)
    const cancelResult = await dialogAction(cdp, '取消')
    await sleep(700)
    evidence.steps.push({
      step: 'A-T08 取消二次确认', visible, cancel: cancelResult,
      resolveRequests: cdp.resolveLog.length - beforeCancel,
      messages: await cdp.eval(MESSAGES),
    })

    const beforeBatch = await termCounts(BOOK)

    // —— 场景二：第 17 条 500、第 18 条断网 ——
    await cdp.eval(`Array.from(document.querySelectorAll('button')).find((b) => (b.textContent || '').includes('批量采纳候选'))?.click()`)
    const confirmResult = await dialogAction(cdp, '确定')
    await sleep(1200)
    await cdp.waitFor(`!document.querySelector('.el-message-box')`, 60000, '批量执行结束')
    await sleep(1500)
    const afterFaults = await termCounts(BOOK)
    evidence.steps.push({
      step: 'A-T08 第17条失败 / 第18条断网', confirm: confirmResult,
      resolveRequests: cdp.resolveLog.length - beforeCancel,
      badges: await cdp.eval(RESULT_BADGES),
      messages: await cdp.eval(MESSAGES),
      termsWrittenBefore: Object.values(beforeBatch).filter((n) => n > 0).length,
      termsWrittenAfter: Object.values(afterFaults).filter((n) => n > 0).length,
      duplicated: Object.entries(afterFaults).filter(([, n]) => n > 1),
    })

    // —— 场景三：重试只含 failed/skipped，成功项不重跑、不重复写 ——
    cdp.faults = false
    const beforeRetry = cdp.resolveLog.length
    await cdp.eval(`Array.from(document.querySelectorAll('button')).find((b) => (b.textContent || '').includes('重试'))?.click()`)
    const retryConfirm = await dialogAction(cdp, '确定')
    await sleep(1500)
    await cdp.waitFor(`!document.querySelector('.el-message-box')`, 60000, '重试结束')
    await sleep(1500)
    const afterRetry = await termCounts(BOOK)
    evidence.steps.push({
      step: 'A-T08 重试失败/未执行项', confirm: retryConfirm,
      retryRequests: cdp.resolveLog.length - beforeRetry,
      badges: await cdp.eval(RESULT_BADGES),
      messages: await cdp.eval(MESSAGES),
      termsWrittenAfterRetry: Object.values(afterRetry).filter((n) => n > 0).length,
      duplicatedAfterRetry: Object.entries(afterRetry).filter(([, n]) => n > 1),
      allOnce: Object.values(afterRetry).every((n) => n <= 1),
    })

    const shot = await cdp.send('Page.captureScreenshot', { format: 'png' })
    writeFileSync(join(OUT, 'spec08-a08-batch.png'), Buffer.from(shot.data, 'base64'))
    writeFileSync(join(OUT, 'spec08-a08-evidence.json'), JSON.stringify(evidence, null, 2))
    console.log(JSON.stringify(evidence, null, 2))
  } finally {
    try {
      if (cdp) {
        evidence.resolveRequestsTotal = cdp.resolveLog.length
        try {
          const shot = await cdp.send('Page.captureScreenshot', { format: 'png' })
          writeFileSync(join(OUT, 'spec08-a08-batch.png'), Buffer.from(shot.data, 'base64'))
        } catch { /* 页面已关就算了 */ }
      }
      writeFileSync(join(OUT, 'spec08-a08-evidence.json'), JSON.stringify(evidence, null, 2))
    } catch { /* ignore */ }
    try { cdp?.ws.close() } catch { /* ignore */ }
    chrome.kill()
  }
}

main().catch((error) => {
  console.error('补验失败：', error.message)
  process.exit(1)
})
