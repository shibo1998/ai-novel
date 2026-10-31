// 规格06 §11：执行真实SFC的setup逻辑，验证跨页面异步流程；不启动服务或调用模型。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const { compileScript, parse } = require('@vue/compiler-sfc')
const ts = require('typescript')
const vue = require('vue')
const storage = new Map()
globalThis.localStorage = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
}
const flush = async () => {
  await vue.nextTick()
  await new Promise((resolve) => setImmediate(resolve))
}
function deferred() {
  let resolve
  const promise = new Promise((done) => (resolve = done))
  return { promise, resolve }
}
function setupSfc(relative, props, imports) {
  const filename = fileURLToPath(new URL(relative, import.meta.url))
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
  const compiled = compileScript(descriptor, { id: 'spec06-test' })
  const js = ts.transpileModule(compiled.content, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
  const hooks = []
  const messages = []
  const events = []
  const log = (kind) => (value) => messages.push([kind, value])
  const defaults = {
    vue: { ...vue, onUnmounted: (hook) => hooks.push(hook) },
    axios: { isAxiosError: () => false },
    '@/utils/prose': {
      buildProseMapping: () => ({ paragraphs: [], lineToParagraph: [] }),
      paragraphForLine: () => null,
      paragraphForUniqueQuote: () => null,
    },
    '@/utils/stream': {
      StreamTextBuffer: class {
        flush() {}
        dispose() {}
        append() {}
      },
      updateFollowState: () => ({ following: true, showReturn: false }),
    },
    '@/utils/errors': { recordError() {}, reportValidation() {} },
    '@/utils/tasks': {
      withTask: async (_options, execute) => execute(new AbortController().signal, {}),
    },
    '@/utils/editing': { registerDirtyGuard: () => () => {} },
    'element-plus': {
      ElMessage: {
        success: log('success'),
        warning: log('warning'),
        error: log('error'),
        info: log('info'),
      },
      ElMessageBox: { confirm: async () => undefined },
    },
  }
  const module = { exports: {} }
  new Function('require', 'module', 'exports', js)(
    (name) => {
      if (name in imports) return imports[name]
      if (name in defaults) return defaults[name]
      if (name.endsWith('.vue')) return {}
      throw new Error(`未模拟的组件依赖：${name}`)
    },
    module,
    module.exports,
  )
  const scope = vue.effectScope()
  const state = scope.run(() =>
    module.exports.default.setup(props, { expose() {}, emit: (...args) => events.push(args) }),
  )
  return {
    state,
    messages,
    events,
    unmount() {
      hooks.forEach((hook) => hook())
      scope.stop()
    },
  }
}
function workbench(status = 'mech_check') {
  return { status, no: 1, row: { current_version: 1, state_revision: 5 }, prose: null }
}
for (const phase of ['mechcheck', 'review', 'unmount']) {
  test(`一键编排等待${phase}时切换会话，不继续审校或改写其他章节`, async () => {
    const route = vue.reactive({ params: { name: '书A', no: '1' } })
    const pending = deferred()
    const calls = []
    const api = {
      getWorkbench: async () => workbench(),
      listVersions: async () => [],
      getCheckStatus: async () => null,
      getDraft: async () => null,
      getDeslop: async () => null,
      runMechcheck: (book, no) => {
        calls.push(['mechcheck', book, no])
        return phase === 'review' ? Promise.resolve({ status: 'llm_review' }) : pending.promise
      },
      runReview: (book, no) => {
        calls.push(['review', book, no])
        return pending.promise
      },
    }
    const component = setupSfc(
      '../src/views/WorkbenchView.vue',
      {},
      {
        '@/api': api,
        'vue-router': { useRoute: () => route, useRouter: () => ({ push() {} }) },
        '@/stores/book': { useBookStore: () => ({ setCurrentBook() {}, rememberChapter() {} }) },
        '@/utils/status': {
          tagType: () => 'info',
          statusLabel: String,
          verdictLabel: String,
          levelLabel: String,
          ruleLabel: String,
          stageLabel: String,
        },
      },
    )
    try {
      await flush()
      const running = component.state.runAuto()
      await flush()
      await flush()
      if (phase === 'unmount') component.unmount()
      else {
        route.params.name = '书B'
        route.params.no = '2'
        await flush()
      }
      pending.resolve({ status: phase === 'review' ? 'mech_check' : 'llm_review', verdict: 'fix' })
      await running
      assert.deepEqual(
        calls,
        phase === 'review'
          ? [
              ['mechcheck', '书A', 1],
              ['review', '书A', 1],
            ]
          : [['mechcheck', '书A', 1]],
        JSON.stringify(component.state.autoLog.value),
      )
      if (phase !== 'unmount') assert.equal(component.state.autoRunning.value, false)
    } finally {
      component.unmount()
    }
  })
}

test('另一页面已采纳后，放弃响应保留applied并刷新正文', async () => {
  const ready = {
    ok: true,
    candidate_id: 'candidate',
    status: 'ready',
    ready: true,
    applied: false,
    source_revision: 5,
    source_fingerprint: 'source',
    candidate_fingerprint: 'candidate',
    checks: {},
    v3: {},
    edits: [],
    warnings: [],
    receipt: {},
  }
  const applied = {
    ...ready,
    status: 'applied',
    ready: false,
    applied: true,
    receipt: { state_revision: 7 },
  }
  const component = setupSfc(
    '../src/components/PolishPanel.vue',
    {
      book: '书A',
      no: 1,
      revision: 5,
      status: 'pending_finalize',
    },
    {
      '@/api': {
        listPolishCandidates: async () => [],
        discardPolishCandidate: async () => applied,
      },
    },
  )
  try {
    await flush()
    component.state.candidate.value = ready
    component.state.candidates.value = [ready]
    await component.state.discard()
    assert.equal(component.state.candidate.value.status, 'applied')
    assert.equal(component.state.candidate.value.applied, true)
    assert.ok(component.events.some(([name]) => name === 'applied'))
    assert.equal(
      component.messages.some(([kind]) => kind === 'success'),
      false,
    )
  } finally {
    component.unmount()
  }
})
