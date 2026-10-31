// 规格08 阶段B 浏览器实测：规划页树渲染 / legacy 启用面板 / 确认框中文按钮（locale）。
//
// 只用本机 Chrome 的 DevTools Protocol，**不引入任何新依赖**（Node 22 自带 WebSocket）。
// 只连隔离实例（默认 127.0.0.1:8899，临时 AINOVEL_HOME），不碰真实书。
//
// 用法：node scripts/spec08_browser_planning.mjs [baseUrl] [outDir]
import { spawn } from 'node:child_process'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const BASE = process.argv[2] || 'http://127.0.0.1:8899'
const OUT = process.argv[3] || '.pi/baseline'
const ROLLING = '规划滚动书'
const LEGACY = '规格08临时书'
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const DEBUG_PORT = 9412

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

/** 等页面文本出现（数据异步拉取，不能只等 load）。 */
async function waitForText(cdp, text, timeout = 20000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const hit = await cdp.eval(`document.body.innerText.includes(${JSON.stringify(text)})`)
    if (hit) return true
    await sleep(200)
  }
  throw new Error(`等待文本「${text}」超时`)
}

async function main() {
  mkdirSync(OUT, { recursive: true })
  const profile = mkdtempSync(join(tmpdir(), 'ainovel-cdp-'))
  const chrome = spawn(CHROME, [
    '--headless=new',
    `--remote-debugging-port=${DEBUG_PORT}`,
    `--user-data-dir=${profile}`,
    '--window-size=1280,900',
    '--no-first-run',
    '--no-default-browser-check',
    'about:blank',
  ], { stdio: 'ignore' })

  const evidence = { base: BASE, steps: [], at: new Date().toISOString() }
  let cdp
  try {
    cdp = await connect()
    await cdp.send('Page.enable')
    await cdp.send('Runtime.enable')
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: 1280, height: 900, deviceScaleFactor: 1, mobile: false,
    })

    // —— B1：rolling 书规划页树渲染与 next_actions ——
    await cdp.send('Page.navigate', {
      url: `${BASE}/#/book/${encodeURIComponent(ROLLING)}/planning`,
    })
    await waitForText(cdp, '规划树')
    await sleep(600) // 树数据异步展开后再采样
    const rollingInfo = await cdp.eval(`(() => {
      const treeNodes = Array.from(document.querySelectorAll('.el-tree-node__content'))
        .map((n) => (n.textContent || '').trim())
      const next = Array.from(document.querySelectorAll('div,span'))
        .map((n) => (n.textContent || '').trim())
        .find((t) => t.startsWith('下一步：'))
      const tags = Array.from(document.querySelectorAll('.el-tag'))
        .map((t) => (t.textContent || '').trim())
      return { treeNodes, next, tags }
    })()`)
    evidence.steps.push({ step: 'B1 rolling 规划页', result: rollingInfo })

    const okB1 = rollingInfo.next?.includes('杀手追缉')
      && rollingInfo.treeNodes.some((t) => t.includes('全书'))
      && rollingInfo.treeNodes.some((t) => t.includes('青云坊'))
    const shot1 = await cdp.send('Page.captureScreenshot', { format: 'png' })
    writeFileSync(join(OUT, 'spec08-b1-planning-rolling.png'), Buffer.from(shot1.data, 'base64'))

    // —— B2：legacy 书启用面板 ——
    await cdp.send('Page.navigate', {
      url: `${BASE}/#/book/${encodeURIComponent(LEGACY)}/planning`,
    })
    await waitForText(cdp, '本书仍是旧模式')
    const legacyInfo = await cdp.eval(`(() => ({
      hasEnableButton: Array.from(document.querySelectorAll('button'))
        .some((b) => (b.textContent || '').includes('启用内容规划')),
    }))()`)
    evidence.steps.push({ step: 'B2 legacy 启用面板', result: legacyInfo })

    // —— B3：ElMessageBox 确认框按钮必须是中文（locale 修复验证）——
    await cdp.eval(`(() => {
      const btn = Array.from(document.querySelectorAll('button'))
        .find((b) => (b.textContent || '').includes('启用内容规划'))
      if (!btn) return 'button-not-found'
      btn.click()
      return 'clicked'
    })()`)
    await waitForText(cdp, '确认启用')
    await sleep(400)
    const dialogInfo = await cdp.eval(`(() => {
      const box = document.querySelector('.el-message-box')
      const buttons = Array.from(document.querySelectorAll('.el-message-box__btns button'))
        .map((b) => (b.textContent || '').trim())
      return { title: box ? (box.querySelector('.el-message-box__title')?.textContent || '') : '',
               buttons }
    })()`)
    const okB3 = dialogInfo.buttons.includes('确定') && dialogInfo.buttons.includes('取消')
      && !dialogInfo.buttons.includes('OK') && !dialogInfo.buttons.includes('Cancel')
    evidence.steps.push({ step: 'B3 确认框按钮 locale', result: dialogInfo, pass: okB3 })

    const shot2 = await cdp.send('Page.captureScreenshot', { format: 'png' })
    writeFileSync(join(OUT, 'spec08-b3-messagebox-locale.png'), Buffer.from(shot2.data, 'base64'))

    // 取消，不真正启用（保持 legacy 书原状）
    await cdp.eval(`(() => {
      const cancel = Array.from(document.querySelectorAll('.el-message-box__btns button'))
        .find((b) => (b.textContent || '').trim() === '取消')
      cancel?.click()
      return 'cancelled'
    })()`)
    await sleep(300)

    evidence.summary = {
      b1TreeAndNextAction: !!okB1,
      b2LegacyPanel: !!legacyInfo.hasEnableButton,
      b3ChineseButtons: okB3,
    }
    writeFileSync(join(OUT, 'spec08-browser-planning.json'), JSON.stringify(evidence, null, 2))
    console.log(JSON.stringify(evidence, null, 2))
  } finally {
    try { cdp?.ws.close() } catch { /* ignore */ }
    chrome.kill()
  }
}

main().catch((error) => {
  console.error('规划页浏览器实测失败：', error.message)
  process.exit(1)
})
