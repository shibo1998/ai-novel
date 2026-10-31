// 规格11 §4/6：真实 SFC、composable、API 的内存离线验收；不访问服务、模型或书稿。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
const require = createRequire(import.meta.url)
const ts = require('typescript'),
  vue = require('vue')
const { parse, compileScript, compileTemplate } = require('@vue/compiler-sfc')
const source = (path) =>
  readFileSync(fileURLToPath(new URL(`../src/${path}`, import.meta.url)), 'utf8')
const flush = async () => {
  await vue.nextTick()
  await new Promise((r) => setImmediate(r))
  await vue.nextTick()
}
function deferred() {
  let resolve, reject
  const promise = new Promise((a, b) => {
    resolve = a
    reject = b
  })
  return { promise, resolve, reject }
}
function evaluate(text, imports) {
  const module = { exports: {} }
  const js = ts.transpileModule(text, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
  new Function(
    'require',
    'module',
    'exports',
    'setInterval',
    'clearInterval',
    'setTimeout',
    'clearTimeout',
    js,
  )(
    (name) => {
      if (name in imports) return imports[name]
      if (name.endsWith('.vue')) return {}
      throw new Error(`未模拟依赖：${name}`)
    },
    module,
    module.exports,
    () => 1,
    () => {},
    () => 1,
    () => {},
  )
  return module.exports
}
const context = (book = '演示书', extra = {}) => ({
  schema_version: 1,
  book,
  genre_template: 'urban',
  genre: '都市',
  eligible: true,
  reason: '',
  etag: '"context-v1"',
  ...extra,
})
const idea = (n) => ({
  title: `演示方案${n}`,
  logline: `演示主角${n}解决独立冲突${n}`,
  synopsis: `演示简介${n}`,
  protagonist: `主角${n}`,
  core_conflict: `核心冲突${n}`,
  selling_point: `创作卖点${n}`,
  volume_goal: `首卷目标${n}`,
  volume_hook: `卷末钩子${n}`,
})
const result = (book = '演示书', extra = {}) => ({
  schema_version: 1,
  book,
  genre_template: 'urban',
  genre: '都市',
  context_etag: '"context-v1"',
  request_id: 'demo-request-1',
  ideas: [1, 2, 3].map(idea),
  ...extra,
})
const httpError = (status) => ({
  code: 'ERR_BAD_RESPONSE',
  response: { status, data: { detail: { code: `DEMO_${status}`, message: `演示错误${status}` } } },
})
function mock(overrides = {}) {
  const calls = [],
    server = { context: context(), result: result() }
  const handlers = {
    context: async (b) => ({ ...server.context, book: b }),
    generate: async (b) => ({ ...server.result, book: b }),
    ...overrides,
  }
  const api = Object.fromEntries(
    Object.entries(handlers).map(([name, fn]) => [
      name,
      async (...args) => {
        calls.push([name, args])
        return fn(...args)
      },
    ]),
  )
  return { api, calls, server }
}
function harness(
  m = mock(),
  confirm = async () => 'confirm',
  component = null,
  externalProps = {},
  onEmit = () => {},
) {
  const hooks = [],
    events = [],
    messages = [],
    book = vue.ref('演示书'),
    blocked = vue.ref(false)
  const form = vue.reactive({ mode: 'prepare', count: 10, logline: '', requirements: '' })
  const fakeVue = { ...vue, onUnmounted: (fn) => hooks.push(fn) }
  const ElMessage = Object.assign(
    () => ({ close() {} }),
    Object.fromEntries(
      ['warning', 'success', 'error'].map((kind) => [
        kind,
        (text) => {
          messages.push([kind, text])
          return { close() {} }
        },
      ]),
    ),
  )
  const imports = {
    vue: fakeVue,
    'element-plus': { ElMessage, ElMessageBox: { confirm } },
    '@/api/storyIdeas': { storyIdeasApi: m.api },
  }
  const errors = evaluate(source('utils/errors.ts'), imports)
  const tasks = evaluate(source('utils/tasks.ts'), { vue: fakeVue, './errors': errors })
  const utils = evaluate(source('utils/storyIdeas.ts'), {})
  Object.assign(imports, {
    '@/utils/errors': errors,
    '@/utils/tasks': tasks,
    '@/utils/storyIdeas': utils,
  })
  const composable = evaluate(source('composables/useStoryIdeas.ts'), imports)
  imports['@/composables/useStoryIdeas'] = composable
  const scope = vue.effectScope()
  let state, render, props
  const productionCalls = [],
    route = vue.reactive({ params: { name: '演示书' }, query: {} }),
    guard = { dirty: null }
  if (component === 'views/VolumeView') {
    const productionApi = {
      volumes: async (b) => ({
        book: b,
        title: '正式书名不变',
        etag: 'production-only-version',
        volumes: [],
        jobs: [],
        warnings: [],
      }),
      create: async (b, request, etag) => {
        productionCalls.push(['create', b, request, etag])
        return {
          id: '11111111-1111-4111-8111-111111111111',
          book: b,
          status: 'preparing',
          phase: 'preparing',
          numbers: [1],
          current_chapter: null,
          completed: [],
          applied: [],
          reason: null,
          request,
          created_at: '2026-10-30',
          updated_at: '2026-10-30',
          etag: 'job-v1',
        }
      },
      job: async () => {
        throw new Error('测试不应读取未登记任务')
      },
    }
    const productionUtils = evaluate(source('utils/production.ts'), {})
    const productionImports = {
      vue: fakeVue,
      '@/api/production': { productionApi },
      '@/utils/production': productionUtils,
      '@/utils/errors': errors,
    }
    Object.assign(imports, productionImports, {
      '@/composables/useVolumeProduction': evaluate(
        source('composables/useVolumeProduction.ts'),
        productionImports,
      ),
      'vue-router': { useRoute: () => route, useRouter: () => ({ push() {} }) },
      '@/stores/book': { useBookStore: () => ({ setCurrentBook() {} }) },
      '@/utils/editing': {
        registerDirtyGuard: (_fn, dirty) => {
          guard.dirty = dirty
          return () => {}
        },
      },
      '@/utils/status': { statusLabel: (v) => v },
    })
  }
  if (component) {
    props = vue.reactive({ book: book.value, form, disabled: false, ...externalProps })
    const filename = `${component}.vue`,
      { descriptor, errors } = parse(source(filename), { filename })
    assert.deepEqual(errors, [])
    const script = compileScript(descriptor, { id: 'spec11' })
    const template = compileTemplate({
      source: descriptor.template.content,
      filename,
      id: 'spec11',
    })
    assert.deepEqual(template.errors, [])
    state = scope.run(() =>
      evaluate(script.content, imports).default.setup(props, {
        expose() {},
        emit: (...args) => {
          events.push(args)
          onEmit(...args)
        },
      }),
    )
    render = evaluate(template.code, {
      vue: { ...vue, resolveComponent: (name) => name, withDirectives: (v) => v },
    }).render
  } else state = scope.run(() => composable.useStoryIdeas(book, form, blocked))
  return {
    state,
    form,
    book,
    blocked,
    props,
    events,
    messages,
    tasks,
    utils,
    route,
    productionCalls,
    guard,
    render: () => render(vue.proxyRefs({ ...props, ...state }), []),
    unmount(reset = true) {
      hooks.forEach((fn) => fn())
      scope.stop()
      if (reset) tasks.resetTasksForTest()
    },
  }
}
const generations = (m) => m.calls.filter(([name]) => name === 'generate')
function children(node) {
  if (Array.isArray(node)) return node.flatMap(children)
  if (!node || typeof node !== 'object') return []
  const nested = Array.isArray(node.children)
    ? node.children
    : typeof node.children === 'object' && node.children
      ? Object.values(node.children)
          .filter((v) => typeof v === 'function')
          .map((fn) => fn())
      : []
  return [node, ...children(nested)]
}
const textOf = (node) =>
  typeof node === 'string' || typeof node === 'number'
    ? String(node)
    : Array.isArray(node)
      ? node.map(textOf).join('')
      : typeof node?.children === 'object' && node.children && !Array.isArray(node.children)
        ? Object.values(node.children)
            .filter((v) => typeof v === 'function')
            .map((fn) => textOf(fn()))
            .join('')
        : textOf(node?.children ?? '')

test('真实 API 只发送两输入字段、独立 If-Match、timeout0/AbortSignal 与会话错误上下文', async () => {
  const calls = [],
    http = {
      get: async (...args) => {
        calls.push(['get', ...args])
        return { data: context('空格 书'), headers: { etag: '"context-v1"' } }
      },
      post: async (...args) => {
        calls.push(['post', ...args])
        return { data: result('空格 书') }
      },
    }
  const api = evaluate(source('api/storyIdeas.ts'), { './client': { http } }).storyIdeasApi
  const signal = new AbortController().signal
  await api.context('空格 书', signal)
  await api.generate(
    '空格 书',
    { preferences: '', previous_loglines: [], genre_template: 'forbidden', logline: 'forbidden' },
    '"context-v1"',
    signal,
  )
  assert.equal(calls[0][1], `/api/books/${encodeURIComponent('空格 书')}/story-ideas/context`)
  assert.equal(calls[1][1], `/api/books/${encodeURIComponent('空格 书')}/story-ideas/generate`)
  assert.deepEqual(calls[1][2], { preferences: '', previous_loglines: [] })
  assert.equal(calls[1][3].timeout, 0)
  assert.equal(calls[1][3].signal, signal)
  assert.equal(calls[1][3].headers['If-Match'], '"context-v1"')
  assert.equal(calls[0][2].signal, signal)
  assert.equal(calls[1][3].errorContext.book, '空格 书')
  assert.equal(calls[1][3].errorContext.write, false)
})
test('返回体严验字段/数量/长度/版本/题材/去标点重复；确定性两字段映射', () => {
  const h = harness()
  try {
    h.utils.assertStoryContext(context(), '演示书')
    for (const [genre_template, genre] of [
      ['urban', '都市'],
      ['xuanhuan', '玄幻'],
      ['romance', '言情'],
      ['suspense', '悬疑'],
    ])
      h.utils.assertStoryContext(context(undefined, { genre_template, genre }), '演示书')
    h.utils.assertStoryResult(result(), context())
    for (const invalid of [
      result('错书'),
      result(undefined, { schema_version: 2 }),
      result(undefined, { genre_template: 'romance' }),
      result(undefined, { ideas: [idea(1)] }),
      result(undefined, { ideas: [idea(1), idea(2), { ...idea(3), title: 'x'.repeat(81) }] }),
      result(undefined, { ideas: [idea(1), idea(2), { ...idea(3), volume_hook: '' }] }),
      result(undefined, {
        ideas: [idea(1), { ...idea(2), logline: '演示主角1，解决独立冲突1！' }, idea(3)],
      }),
    ])
      assert.throws(() => h.utils.assertStoryResult(invalid, context()))
    assert.throws(() =>
      h.utils.assertStoryContext(context(undefined, { genre_template: 'guess' }), '演示书'),
    )
    const mapped = h.utils.storyIdeaToForm(idea(2))
    assert.deepEqual(Object.keys(mapped).sort(), ['logline', 'requirements'])
    assert.match(mapped.requirements, /首卷目标：首卷目标2/)
    assert.match(mapped.requirements, /卷末钩子：卷末钩子2/)
  } finally {
    h.unmount()
  }
})
test('空想法可生成且双击只有一次；前后独立重读 context，换批带3logline且失败保留卡和手填', async () => {
  const wait = deferred(),
    m = mock({ generate: async () => wait.promise }),
    h = harness(m)
  try {
    await flush()
    const first = h.state.generate()
    await h.state.generate()
    await flush()
    assert.equal(generations(m).length, 1)
    assert.equal(h.state.prepareBlocked.value, true)
    assert.equal(h.form.logline, '')
    wait.resolve(result())
    await first
    assert.equal(h.state.batch.value.ideas.length, 3)
    assert.ok(m.calls.filter(([n]) => n === 'context').length >= 3)
    h.form.logline = '手填保留'
    h.form.requirements = '手填要求'
    await flush()
    m.api.generate = async (...args) => {
      m.calls.push(['generate', args])
      throw httpError(502)
    }
    await h.state.generate()
    assert.deepEqual(
      generations(m)[1][1][1].previous_loglines,
      result().ideas.map((v) => v.logline),
    )
    assert.equal(h.state.batch.value.request_id, 'demo-request-1')
    assert.equal(h.form.logline, '手填保留')
    assert.equal(h.state.unknown.value, false)
  } finally {
    h.unmount()
  }
})
test('选用仅填两本地字段，替换预览取消零修改/零production；确认期间输入变化甚至改回也不覆盖', async () => {
  const dialog = deferred()
  let dialogs = 0,
    message
  const m = mock(),
    h = harness(m, (v) => {
      dialogs++
      message = v
      return dialog.promise
    })
  try {
    await flush()
    await h.state.generate()
    h.form.logline = '手写故事'
    h.form.requirements = '手写要求'
    h.form.count = 17
    const first = h.state.select(0)
    await flush()
    await h.state.select(1)
    assert.equal(dialogs, 1)
    assert.match(textOf(message), /手写故事/)
    assert.match(textOf(message), /演示主角1/)
    dialog.reject('cancel')
    await first
    assert.equal(h.form.logline, '手写故事')
    assert.equal(h.form.count, 17)
    const late = deferred()
    h.unmount()
    const c = harness(m, () => late.promise)
    try {
      await flush()
      await c.state.generate()
      c.form.logline = '原文'
      const pending = c.state.select(0)
      await flush()
      c.form.logline = '改动'
      c.form.logline = '原文'
      late.resolve('confirm')
      await pending
      assert.equal(c.form.logline, '原文')
    } finally {
      c.unmount()
    }
    assert.ok(m.calls.every(([n]) => ['context', 'generate'].includes(n)))
  } finally {
    h.unmount()
  }
  const selected = harness(mock())
  try {
    await flush()
    await selected.state.generate()
    selected.form.count = 21
    await selected.state.select(1)
    assert.equal(selected.form.logline, idea(2).logline)
    assert.equal(selected.form.count, 21)
    assert.match(selected.form.requirements, /核心冲突：核心冲突2/)
    assert.equal(selected.state.selected.value, 1)
  } finally {
    selected.unmount()
  }
})
test('生成前/后及替换确认中 context变动或不适用时拒绝，不猜默认题材', async () => {
  const m = mock(),
    h = harness(m)
  try {
    await flush()
    m.server.context.eligible = false
    m.server.context.reason = '已有首卷'
    await h.state.generate()
    assert.equal(generations(m).length, 0)
    assert.match(h.state.error.value, /已有首卷/)
  } finally {
    h.unmount()
  }
  const wait = deferred(),
    after = mock({ generate: async () => wait.promise }),
    a = harness(after)
  try {
    await flush()
    const pending = a.state.generate()
    await flush()
    after.server.context.etag = '"v2"'
    wait.resolve(result())
    await pending
    assert.equal(a.state.batch.value, null)
  } finally {
    a.unmount()
  }
  const dialog = deferred(),
    changed = mock(),
    c = harness(changed, () => dialog.promise)
  try {
    await flush()
    await c.state.generate()
    c.form.logline = '原文'
    const pending = c.state.select(0)
    await flush()
    changed.server.context.etag = '"v2"'
    dialog.resolve('confirm')
    await pending
    assert.equal(c.form.logline, '原文')
    assert.equal(c.state.stale.value, true)
  } finally {
    c.unmount()
  }
})
test('跨书/卸载/生成期间输入变化的晚到响应不会串候选或覆盖表单', async () => {
  for (const change of ['book', 'input', 'unmount']) {
    const wait = deferred(),
      m = mock({ generate: async () => wait.promise }),
      h = harness(m)
    try {
      await flush()
      const pending = h.state.generate()
      await flush()
      if (change === 'book') h.book.value = '另一书'
      else if (change === 'input') h.form.logline = '晚到修改'
      else h.unmount()
      await flush()
      wait.resolve(result())
      await pending
      assert.equal(h.state.batch.value, null)
      assert.notEqual(h.form.logline, idea(1).logline)
    } finally {
      h.unmount()
    }
  }
})
test('停止等待/网络未知阻断prepare，无自动重发；重试取消仍阻断、接受再次计费才显式调用', async () => {
  for (const stopped of [false, true]) {
    let dialogs = 0,
      accept = false
    const wait = deferred(),
      m = mock({
        generate: async (_b, _p, _e, signal) => {
          signal.addEventListener('abort', () => wait.reject({ code: 'ERR_CANCELED' }), {
            once: true,
          })
          return wait.promise
        },
      })
    const h = harness(m, async () => {
      dialogs++
      if (!accept) throw 'cancel'
      return 'confirm'
    })
    try {
      await flush()
      const pending = h.state.generate()
      await flush()
      if (stopped) h.state.stop()
      else wait.reject({ code: 'ERR_NETWORK', message: '断线' })
      await pending
      assert.equal(h.state.unknown.value, true)
      assert.equal(h.state.prepareBlocked.value, true)
      assert.match(h.state.notice.value, /费用|计费/)
      assert.match(h.state.notice.value, /无法.*取回|无法.*恢复/)
      await h.state.generate()
      assert.equal(dialogs, 1)
      assert.equal(generations(m).length, 1)
      assert.equal(h.state.prepareBlocked.value, true)
      accept = true
      m.api.generate = async (...args) => {
        m.calls.push(['generate', args])
        return result()
      }
      await h.state.generate()
      assert.equal(dialogs, 2)
      assert.equal(generations(m).length, 2)
      assert.equal(h.state.unknown.value, false)
      assert.equal(h.state.prepareBlocked.value, false)
      assert.ok(h.tasks.tasks.every((t) => t.operation === '同题材故事候选' && !t.write))
    } finally {
      h.unmount()
    }
  }
})
test('真实 Picker SFC 三卡完整对比、只读题材/纯文本、按钮执行真实生成与选用', async () => {
  const m = mock(),
    h = harness(m, undefined, 'components/StoryIdeaPicker')
  try {
    await flush()
    assert.match(textOf(h.render()), /当前题材：都市/)
    const initial = children(h.render()).find((n) => n.props?.['data-action'] === 'generate')
    assert.ok(initial)
    await initial.props.onClick()
    await flush()
    assert.equal(
      children(h.render()).filter((n) => n.props?.class?.includes?.('story-idea-card')).length,
      3,
    )
    assert.match(textOf(h.render()), /首卷目标/)
    assert.match(textOf(h.render()), /卷末钩子/)
    const choose = children(h.render()).find((n) => n.props?.['data-action'] === 'select')
    await choose.props.onClick()
    assert.equal(h.form.logline, idea(1).logline)
    assert.equal(generations(m).length, 1)
    assert.ok(h.events.some(([n, , v]) => n === 'blocking' && v === true))
    assert.ok(!children(h.render()).some((n) => n.props?.innerHTML))
  } finally {
    h.unmount()
  }
})

test('真实 VolumeView 接入 Picker：等待/未知均阻断 prepare，选用保留 count/书名/题材且 dirty，只有作者手动 prepare 才请求 production', async () => {
  const m = mock(),
    v = harness(m, undefined, 'views/VolumeView')
  let picker
  try {
    await flush()
    const node = children(v.render()).find((n) => n.props?.onBlocking)
    assert.ok(node)
    assert.equal(node.props.form, v.state.form)
    picker = harness(
      m,
      undefined,
      'components/StoryIdeaPicker',
      { form: v.state.form },
      (name, b, flag) => {
        if (name === 'blocking') node.props.onBlocking(b, flag)
      },
    )
    await flush()
    v.state.form.count = 23
    v.state.submittedForm.value = JSON.stringify(v.state.form)
    assert.equal(v.guard.dirty(), false)
    const wait = deferred()
    m.api.generate = async (...args) => {
      m.calls.push(['generate', args])
      return wait.promise
    }
    const pending = picker.state.ideas.generate()
    await flush()
    await v.state.prepare()
    assert.deepEqual(v.productionCalls, [])
    wait.reject({ code: 'ERR_NETWORK' })
    await pending
    await v.state.prepare()
    assert.deepEqual(v.productionCalls, [])
    m.api.generate = async (...args) => {
      m.calls.push(['generate', args])
      return result()
    }
    await picker.state.ideas.generate()
    await picker.state.ideas.select(0)
    await flush()
    assert.equal(v.state.form.count, 23)
    assert.equal(v.state.model.value.title, '正式书名不变')
    assert.equal(picker.state.context.value.genre, '都市')
    assert.equal(v.state.inputDirty.value, true)
    assert.equal(v.guard.dirty(), true)
    assert.deepEqual(v.productionCalls, [])
    assert.ok(!m.calls.some(([name]) => ['production', 'confirm', 'finalize'].includes(name)))
    await v.state.prepare()
    assert.equal(v.productionCalls.length, 1)
    assert.equal(v.productionCalls[0][3], 'production-only-version')
    assert.equal(v.productionCalls[0][2].logline, idea(1).logline)
  } finally {
    picker?.unmount()
    v.unmount()
  }
  const hand = harness(mock(), undefined, 'views/VolumeView')
  try {
    await flush()
    hand.state.form.logline = '旧手填兼容'
    hand.state.setIdeaBlocking('别的书', true)
    await hand.state.prepare()
    assert.equal(hand.productionCalls.length, 1)
    assert.equal(hand.productionCalls[0][2].logline, '旧手填兼容')
  } finally {
    hand.unmount()
  }
})
test('替换确认期间候选/书/禁用变化不填表；选用的 context GET 晚到时输入变化也不覆盖', async () => {
  for (const change of ['candidate', 'book', 'disabled', 'context']) {
    const dialog = deferred(),
      h = harness(mock(), () => dialog.promise)
    try {
      await flush()
      await h.state.generate()
      h.form.requirements = '保留要求'
      const pending = h.state.select(0)
      await flush()
      if (change === 'candidate') h.state.batch.value.ideas[0].title = '变更候选'
      else if (change === 'book') h.book.value = '新书'
      else if (change === 'context') h.state.context.value.etag = '"changed"'
      else h.blocked.value = true
      dialog.resolve('confirm')
      await pending
      assert.equal(h.form.requirements, '保留要求')
      assert.equal(h.form.logline, '')
    } finally {
      h.unmount()
    }
  }
  const m = mock(),
    h = harness(m),
    late = deferred()
  try {
    await flush()
    await h.state.generate()
    m.api.context = async () => late.promise
    const pending = h.state.select(0)
    h.form.logline = '作者晚改'
    late.resolve(context())
    await pending
    assert.equal(h.form.logline, '作者晚改')
  } finally {
    h.unmount()
  }
})
test('纯返回体跨书后未知任务仍保留原书阻断；晚到成功不会伪称取消或串候选', async () => {
  const wait = deferred(),
    m = mock({ generate: async () => wait.promise }),
    h = harness(m)
  try {
    await flush()
    const pending = h.state.generate()
    await flush()
    h.book.value = '另一书'
    await flush()
    wait.resolve(result())
    await pending
    assert.equal(h.state.batch.value, null)
    assert.equal(h.tasks.tasks[0].book, '演示书')
    assert.equal(h.tasks.tasks[0].phase, 'unknown')
    assert.equal(h.state.prepareBlocked.value, false)
    h.book.value = '演示书'
    await flush()
    assert.equal(h.state.prepareBlocked.value, true)
    assert.equal(h.state.batch.value, null)
    assert.equal(generations(m).length, 1)
  } finally {
    h.unmount()
  }
})
test('契约错误409/412/428/502/503有明确失败；上下文读取失败不把旧成功误标计费未知', async () => {
  for (const status of [409, 412, 428, 502, 503]) {
    const m = mock({
        generate: async () => {
          throw httpError(status)
        },
      }),
      h = harness(m)
    try {
      await flush()
      await h.state.generate()
      assert.equal(h.state.batch.value, null)
      assert.equal(h.state.unknown.value, false)
      assert.match(h.state.error.value, new RegExp(String(status)))
      assert.equal(generations(m).length, 1)
      assert.equal(h.form.logline, '')
    } finally {
      h.unmount()
    }
  }
  const m = mock(),
    h = harness(m)
  try {
    await flush()
    await h.state.generate()
    m.api.context = async () => {
      throw { code: 'ERR_NETWORK' }
    }
    await h.state.generate()
    assert.equal(h.state.unknown.value, false)
    assert.equal(h.state.stale.value, true)
    assert.equal(h.state.canSelect.value, false)
    assert.equal(generations(m).length, 1)
  } finally {
    h.unmount()
  }
})

test('初始context晚到跨书隔离，只读失败/不适用不触发模型；偏好超限不能调用', async () => {
  const late = deferred(),
    m = mock({
      context: async (b) =>
        b === '演示书' ? late.promise : context(b, { genre_template: 'suspense', genre: '悬疑' }),
    }),
    h = harness(m)
  try {
    h.book.value = '新书'
    await flush()
    late.resolve(context())
    await flush()
    assert.equal(h.state.context.value.book, '新书')
    assert.equal(h.state.context.value.genre, '悬疑')
    h.state.preferences.value = '字'.repeat(2001)
    await h.state.generate()
    assert.equal(generations(m).length, 0)
    assert.equal(h.state.canGenerate.value, false)
  } finally {
    h.unmount()
  }
})
test('未知重试确认弹窗double click只有一弹窗；弹窗期间输入变更不解除未知也不再次调用', async () => {
  const dialog = deferred()
  let dialogs = 0
  const m = mock({
      generate: async () => {
        throw { code: 'ERR_NETWORK' }
      },
    }),
    h = harness(m, () => {
      dialogs++
      return dialog.promise
    })
  try {
    await flush()
    await h.state.generate()
    const pending = h.state.generate()
    await h.state.generate()
    assert.equal(dialogs, 1)
    assert.equal(h.state.prepareBlocked.value, true)
    h.form.requirements = '弹窗期间修改'
    dialog.resolve('confirm')
    await pending
    assert.equal(generations(m).length, 1)
    assert.equal(h.state.unknown.value, true)
    assert.equal(h.state.prepareBlocked.value, true)
    assert.equal(h.form.requirements, '弹窗期间修改')
  } finally {
    h.unmount()
  }
})
test('真实composable非法结果保留旧卡与输入，不展示占位成功、不变未知；SFC恶意HTML为纯文本', async () => {
  const m = mock(),
    h = harness(m, undefined, 'components/StoryIdeaPicker')
  try {
    await flush()
    m.server.result.ideas[0].title = '<img src=x onerror=alert(1)>'
    await h.state.ideas.generate()
    assert.match(textOf(h.render()), /<img src=x onerror=alert\(1\)>/)
    assert.ok(!children(h.render()).some((v) => v.type === 'img' || v.props?.innerHTML))
    h.form.logline = '旧手填'
    const previous = h.state.batch.value
    m.api.generate = async () => result(undefined, { ideas: [idea(1)] })
    await h.state.ideas.generate()
    assert.equal(h.state.batch.value, previous)
    assert.equal(h.form.logline, '旧手填')
    assert.equal(h.state.unknown.value, false)
    assert.match(h.state.error.value, /不完整/)
  } finally {
    h.unmount()
  }
})

test('Picker核对期间卸载清局部阻断，晚到确认不填表；VolumeView仍按真实会话running/unknown阻断并随结束解除', async () => {
  const dialog = deferred(),
    m = mock(),
    v = harness(m, undefined, 'views/VolumeView')
  let picker
  try {
    await flush()
    const node = children(v.render()).find((n) => n.props?.onBlocking)
    picker = harness(
      m,
      () => dialog.promise,
      'components/StoryIdeaPicker',
      { form: v.state.form },
      (name, b, flag) => {
        if (name === 'blocking') node.props.onBlocking(b, flag)
      },
    )
    await flush()
    await picker.state.ideas.generate()
    v.state.form.logline = '人工输入'
    const selecting = picker.state.ideas.select(0)
    await flush()
    assert.equal(v.state.ideasBlocking.value, true)
    picker.unmount()
    await flush()
    assert.equal(v.state.ideasBlocking.value, false)
    dialog.resolve('confirm')
    await selecting
    assert.equal(v.state.form.logline, '人工输入')
    const task = vue.reactive({ book: '演示书', operation: '同题材故事候选', phase: 'running' })
    v.tasks.tasks.push(task)
    assert.equal(v.state.ideasBlocking.value, true)
    task.phase = 'unknown'
    await flush()
    assert.equal(v.state.ideasBlocking.value, true)
    task.phase = 'completed'
    await flush()
    assert.equal(v.state.ideasBlocking.value, false)
    await v.state.prepare()
    assert.equal(v.productionCalls.length, 1)
  } finally {
    picker?.unmount()
    v.unmount()
  }
})

test('真实withTask成功回执立即解除VolumeView阻断，不依赖其它输入或刷新触发', async () => {
  const v = harness(mock(), undefined, 'views/VolumeView'),
    wait = deferred()
  try {
    await flush()
    const operation = v.tasks.withTask(
      { operation: '同题材故事候选', book: '演示书', write: false },
      async () => wait.promise,
    )
    await flush()
    assert.equal(v.state.ideasBlocking.value, true)
    wait.resolve('已完成')
    await operation
    await flush()
    assert.equal(v.state.ideasBlocking.value, false)
  } finally {
    v.unmount()
  }
})
