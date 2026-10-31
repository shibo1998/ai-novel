// 规格09 §3/6：转译真实工作台 setup 并渲染计量模板；仅用内存响应，不启动服务或访问书稿。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const { compileScript, compileTemplate, parse } = require('@vue/compiler-sfc')
const ts = require('typescript')
const vue = require('vue')
const filename = fileURLToPath(new URL('../src/views/WorkbenchView.vue', import.meta.url))
const source = readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(source, { filename })
assert.deepEqual(errors, [])
const script = compileScript(descriptor, { id: 'spec09-workbench' })
const templateSource = descriptor.template.content
const metricsStart = templateSource.indexOf('<div class="my-2 text-sm" data-prose-metrics>')
const metricsEnd = templateSource.indexOf('<h4>阻断项', metricsStart)
assert.ok(metricsStart >= 0 && metricsEnd > metricsStart, '计量必须接入原检查面板')
const metricsTemplate = templateSource.slice(metricsStart, metricsEnd).trim()
const template = compileTemplate({ source: metricsTemplate, filename, id: 'spec09-metrics' })
assert.deepEqual(template.errors, [])

function transpile(text) {
  return ts.transpileModule(text, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
}
function evaluate(text, imports) {
  const module = { exports: {} }
  new Function('require', 'module', 'exports', transpile(text))(
    (name) => {
      if (name in imports) return imports[name]
      if (name.endsWith('.vue')) return {}
      throw new Error(`未模拟的依赖：${name}`)
    },
    module,
    module.exports,
  )
  return module.exports
}
const { render } = evaluate(template.code, { vue })
const flush = async () => {
  await vue.nextTick()
  await new Promise((resolve) => setImmediate(resolve))
  await vue.nextTick()
}
const unexpected = () => {
  throw new Error('透明计量不应触发写入、路由或网络请求')
}
globalThis.localStorage = { getItem: () => null, setItem() {} }
globalThis.fetch = unexpected

const measured = () => ({
  version: 1,
  paragraphs: { count: 4, mean_chars: 27.555, median_chars: 16, p90_chars: 62, short_ratio: 0.25 },
  simile_cues: {
    status: 'measured',
    scope: 'all_text',
    count: 23,
    per_1000_chars: 1.234,
    positions: [{ line: 1, start: 0, end: 2, cue: '仿佛', quote: '仿佛一阵风' }],
  },
})
const oldResult = () => ({
  score: 75,
  word_count: 300,
  density_penalty: 2,
  ineffective: [],
  hits_count: { blocking: 0, advisory: 1 },
  hits: [{ rule: 'D-14', level: 'warn', line: 1, quote: '仿佛', weight: 2, message: '旧参考项' }],
})
function harness(metrics) {
  const calls = []
  const result = { ...oldResult(), ...(metrics === undefined ? {} : { metrics }) }
  const workbench = {
    no: 1,
    status: 'mech_check',
    planned: true,
    exists: true,
    chapter_numbers: [1],
    outline_generation: { allowed: false },
    row: { state_revision: 1 },
    prose: { _body: metrics?.paragraphs?.count === 0 ? '' : '仅在内存中的离线样稿' },
    mech: {
      verdict: 'block',
      issues: [
        { rule: 'M-04', level: 'block', line: 1, word: '阻断样例', message: '旧阻断' },
        { rule: 'M-03', level: 'warn', line: 1, word: '提醒样例', message: '旧提醒' },
        { rule: 'M-12', level: 'warn', line: 1, word: '参考样例', message: '旧参考' },
      ],
    },
  }
  const responses = {
    getWorkbench: workbench,
    listVersions: [],
    getCheckStatus: { verdict: 'block', stale: false },
    getDraft: null,
    getDeslop: result,
  }
  const api = new Proxy(
    {},
    {
      get:
        (_target, name) =>
        async (...args) => {
          calls.push([name, args])
          if (!(name in responses)) return unexpected()
          return responses[name]
        },
    },
  )
  const unmounted = []
  const component = evaluate(script.content, {
    vue: { ...vue, onUnmounted: (hook) => unmounted.push(hook) },
    axios: { isAxiosError: () => false },
    'element-plus': {
      ElMessage: { success: unexpected, warning: unexpected, error: unexpected, info: unexpected },
      ElMessageBox: { confirm: unexpected, prompt: unexpected },
    },
    'vue-router': {
      useRoute: () => ({ params: { name: '离线测试', no: '1' } }),
      useRouter: () => ({ push: unexpected }),
    },
    '@/api': api,
    '@/stores/book': { useBookStore: () => ({ rememberChapter() {} }) },
    '@/utils/status': {
      levelLabel: String,
      ruleLabel: String,
      stageLabel: String,
      statusLabel: String,
      tagType: () => 'info',
      verdictLabel: String,
    },
    '@/utils/prose': {
      buildProseMapping: () => ({ paragraphs: [] }),
      paragraphForLine: unexpected,
      paragraphForUniqueQuote: unexpected,
    },
    '@/utils/stream': { StreamTextBuffer: class {}, updateFollowState: unexpected },
    '@/utils/errors': { recordError: unexpected, reportValidation: unexpected },
    '@/utils/tasks': { hasPendingWrite: () => false, withTask: unexpected },
    '@/utils/editing': { registerDirtyGuard: () => () => {} },
  }).default
  const scope = vue.effectScope()
  const state = scope.run(() => component.setup({}, { expose() {} }))
  return {
    state,
    calls,
    text: () => textOf(render(vue.proxyRefs(state), [])),
    unmount() {
      for (const hook of unmounted) hook()
      scope.stop()
    },
  }
}
function textOf(node) {
  if (typeof node === 'string') return node
  if (Array.isArray(node)) return node.map(textOf).join('')
  return textOf(node?.children ?? '')
}
async function withMetrics(metrics, check) {
  const component = harness(metrics)
  try {
    await flush()
    await check(component)
  } finally {
    component.unmount()
  }
}

test('有效计量渲染有限小数、段落分布与全文含对白口径；位置列表不冒充总数', async () => {
  await withMetrics(measured(), ({ text }) => {
    const output = text()
    assert.match(output, /段落平均长度：27\.6 字/)
    assert.match(output, /段数：4 · 中位：16\.0 字 · P90：62\.0 字 · ≤10字短段占比：25\.0%/)
    assert.match(output, /显式比喻线索密度：1\.2 处\/千可见字符 · 显式线索数：23/)
    assert.match(output, /去空白可见字符/)
    assert.match(output, /空行分段，段内换行不拆段/)
    assert.match(output, /全文含对白/)
    assert.match(output, /仅作回读参考，非AI生成概率或质量合格线/)
  })
})

test('确实测得零线索时展示 0.0 密度，而不是缺值或绿色通过', async () => {
  const metrics = measured()
  metrics.simile_cues.count = 0
  metrics.simile_cues.per_1000_chars = 0
  await withMetrics(metrics, ({ text }) => {
    assert.match(text(), /显式比喻线索密度：0\.0 处\/千可见字符 · 显式线索数：0/)
    assert.doesNotMatch(text(), /无法计算|未提供|绿色通过/)
  })
})

test('旧结果没有 metrics 时两类计量均显示未提供，不推断为零', async () => {
  await withMetrics(undefined, ({ state, text }) => {
    assert.deepEqual(state.proseMetricsDisplay.value, {
      mean: '未提供',
      distribution: '未提供',
      density: '未提供',
      cues: '未提供',
    })
    assert.match(text(), /段落平均长度：未提供/)
    assert.match(text(), /显式比喻线索密度：未提供/)
    assert.doesNotMatch(text(), /0\.0|段数：0/)
  })
})

test('空正文显示无正文及无法计算，测得 count=0 不被展示为零密度', async () => {
  const metrics = {
    version: 1,
    paragraphs: {
      count: 0,
      mean_chars: null,
      median_chars: null,
      p90_chars: null,
      short_ratio: null,
    },
    simile_cues: { status: 'measured', scope: 'all_text', count: 0, per_1000_chars: null },
  }
  await withMetrics(metrics, ({ text }) => {
    assert.match(text(), /段落平均长度：无正文，无法计算/)
    assert.match(text(), /段数：0 · 中位：无法计算 · P90：无法计算 · ≤10字短段占比：无法计算/)
    assert.match(text(), /显式比喻线索密度：无正文，无法计算 · 显式线索数：0/)
    assert.doesNotMatch(text(), /0\.0 处/)
  })
})

test('配置不可用显示中文原因且段落统计保留，缺原因时保守回退', async () => {
  const metrics = measured()
  metrics.simile_cues = {
    status: 'unavailable',
    scope: 'all_text',
    count: null,
    per_1000_chars: null,
    reason: '比喻线索配置文件缺失',
  }
  await withMetrics(metrics, ({ state, text }) => {
    assert.match(text(), /段落平均长度：27\.6 字/)
    assert.match(text(), /显式比喻线索密度：无法计量：比喻线索配置文件缺失/)
    assert.match(text(), /显式线索数：无法计算/)
    assert.doesNotMatch(text(), /0\.0 处/)
    state.deslopRes.value.metrics.simile_cues.reason = ' '
    assert.match(text(), /无法计量：计量配置不可用/)
  })
})

test('null、NaN、正负 Infinity 与缩放溢出均显示无法计算，不泄漏非有限数值', async () => {
  for (const value of [null, undefined, NaN, Infinity, -Infinity]) {
    const metrics = measured()
    metrics.paragraphs = {
      count: value,
      mean_chars: value,
      median_chars: value,
      p90_chars: value,
      short_ratio: value,
    }
    metrics.simile_cues.count = value
    metrics.simile_cues.per_1000_chars = value
    await withMetrics(metrics, ({ state, text }) => {
      assert.equal(state.metricNumber(value), '无法计算')
      assert.equal(state.metricNumber(Number.MAX_VALUE, '%', 100), '无法计算')
      assert.match(text(), /段落平均长度：无法计算/)
      assert.match(text(), /显式比喻线索密度：无法计算/)
      assert.doesNotMatch(text(), /NaN|Infinity|0\.0 处/)
    })
  }
})

test('原检查模板中只追加只读文本；读取与响应式更新不新增 API、写入、阻断或修复', async () => {
  assert.ok(templateSource.indexOf('③ 检查结果') < metricsStart)
  assert.ok(metricsEnd < templateSource.indexOf('④ 模型审校'))
  assert.doesNotMatch(metricsTemplate, /<el-|<button|@|v-on|v-model|success|blocking|fetch|\/api\//)
  assert.match(source, /computed\(\(\) => proseMetricsText\(deslopRes\.value\?\.metrics\)\)/)
  await withMetrics(measured(), async ({ state, calls, text }) => {
    const expected = ['getWorkbench', 'getCheckStatus', 'getDraft', 'getDeslop', 'listVersions']
    assert.deepEqual(
      calls.map(([name]) => name),
      expected,
    )
    assert.ok(calls.every(([, args]) => args[0] === '离线测试' && args[1] === 1))
    const original = JSON.stringify({
      mech: state.data.value.mech,
      result: { ...state.deslopRes.value, metrics: undefined },
    })
    for (const metrics of [
      undefined,
      {
        ...measured(),
        simile_cues: {
          status: 'unavailable',
          scope: 'all_text',
          count: null,
          per_1000_chars: null,
          reason: '非法配置',
        },
      },
      measured(),
    ]) {
      state.deslopRes.value.metrics = metrics
      text()
      await flush()
      assert.deepEqual(
        calls.map(([name]) => name),
        expected,
      )
      assert.equal(state.blockingHits.value.length, 1)
      assert.equal(state.warningHits.value.length, 1)
      assert.equal(state.referenceHits.value.length, 2)
      assert.equal(state.autoRunning.value, false)
      assert.equal(
        JSON.stringify({
          mech: state.data.value.mech,
          result: { ...state.deslopRes.value, metrics: undefined },
        }),
        original,
      )
    }
  })
})
