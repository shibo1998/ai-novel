// 临时诊断脚本（不属于交付物）：打印页面标题/正文摘要/控制台错误，用于排查渲染问题。
import { spawn } from 'node:child_process'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const BASE = process.argv[2] || 'http://127.0.0.1:8899'
const URL_ = process.argv[3]
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PORT = 9421
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const profile = mkdtempSync(join(tmpdir(), 'ainovel-diag-'))
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, '--window-size=1280,800', '--no-first-run', 'about:blank'], { stdio: 'ignore' })

let ws
for (let i = 0; i < 60; i += 1) {
  try {
    const targets = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()
    const page = targets.find((t) => t.type === 'page')
    ws = new WebSocket(page.webSocketDebuggerUrl)
    await new Promise((res, rej) => { ws.addEventListener('open', res, { once: true }); ws.addEventListener('error', rej, { once: true }) })
    break
  } catch { await sleep(250) }
}

let seq = 0
const pending = new Map()
ws.addEventListener('message', (ev) => {
  const m = JSON.parse(ev.data)
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
  if (m.method === 'Runtime.consoleAPICalled') {
    console.log('CONSOLE:', m.params.type, m.params.args.map((a) => a.value ?? a.description).join(' '))
  }
  if (m.method === 'Runtime.exceptionThrown') {
    console.log('EXCEPTION:', m.params.exceptionDetails.text, m.params.exceptionDetails.exception?.description?.slice(0, 300))
  }
})
const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })) })

await send('Page.enable')
await send('Runtime.enable')
await send('Log.enable')
await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false })
await send('Page.navigate', { url: URL_ })
await sleep(4000)
const r = await send('Runtime.evaluate', {
  expression: `JSON.stringify({ title: document.title, hash: location.hash, bodyText: (document.body.innerText || '').slice(0, 800), prose: document.querySelectorAll('.prose-paragraph').length, panels: document.querySelectorAll('.panel').length })`,
  returnByValue: true,
})
console.log(r.result?.result?.value)
ws.close()
chrome.kill()
