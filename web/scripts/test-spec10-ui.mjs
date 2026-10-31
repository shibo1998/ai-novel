// 规格10 §3/5/6：编译真实 Vue SFC/setup/composable 与 API；内存回执、虚拟时钟，零服务/模型/书稿访问。
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
const copy = (v) => structuredClone(v)
function deferred() {
  let resolve, reject
  const promise = new Promise((a, b) => {
    resolve = a
    reject = b
  })
  return { promise, resolve, reject }
}
function clock() {
  const pending = new Map()
  let id = 0
  return {
    pending,
    set: (fn, ms) => {
      pending.set(++id, { fn, ms })
      return id
    },
    clear: (key) => pending.delete(key),
    async tick() {
      const callbacks = [...pending.values()]
      pending.clear()
      for (const entry of callbacks) {
        assert.equal(entry.ms, 1000)
        entry.fn()
      }
      await flush()
    },
  }
}
function evaluate(text, imports, timers = clock()) {
  const module = { exports: {} }
  const js = ts.transpileModule(text, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
  new Function('require', 'module', 'exports', 'setTimeout', 'clearTimeout', js)(
    (name) => {
      if (name in imports) return imports[name]
      if (name.endsWith('.vue')) return {}
      throw new Error(`未模拟依赖：${name}`)
    },
    module,
    module.exports,
    timers.set,
    timers.clear,
  )
  return module.exports
}
const utils = evaluate(source('utils/production.ts'), {})
const prose = evaluate(source('utils/prose.ts'), {})
const errors = evaluate(source('utils/errors.ts'), {
  vue,
  'element-plus': { ElMessage: () => ({ close() {} }) },
})
const mkJob = (book = '离线书', status = 'awaiting_outline_confirmation', extra = {}) => ({
  id: '11111111-1111-4111-8111-111111111111',
  book,
  status,
  phase: status,
  numbers: [1, 2, 3],
  current_chapter: null,
  completed: [],
  applied: [],
  reason: null,
  request: { mode: 'prepare', volume_no: 1, count: 3, logline: '内存演示故事', requirements: '' },
  created_at: '2026-10-30T00:00:00',
  updated_at: '2026-10-30T00:00:00',
  etag: 'job-v1',
  ...extra,
})
const mkBundle = () => ({
  schema_version: 1,
  outline: { fm: {}, rows: [], rhythm: '' },
  files: {},
  outlines: [1, 2, 3].map((no) => ({
    no,
    fm: { confirmed: false, title: `候选${no}`, extra: { 保留: true } },
    body: `第${no}章细纲（离线演示）`,
  })),
  notes: ['离线演示候选'],
})
const mkChapter = (no) => ({
  no,
  title: `章${no}`,
  body: `离线演示第${no}章\r\n段内换行\r\n\r\n第二段`,
  fingerprint: `fp-${no}`,
  outline_fingerprint: `ofp-${no}`,
  status: 'pending_finalize',
  check: { verdict: 'pass', issues: [] },
  review: { verdict: 'pass', issues: [] },
})
function mockServer(initial = mkJob(), overrides = {}) {
  const server = { j: copy(initial), candidate: mkBundle(), chapters: [], digest: 'digest-v1' },
    calls = []
  const volumes = (book) => ({
    book,
    title: book,
    etag: 'book-v1',
    volumes: [
      {
        no: 1,
        name: '演示卷',
        goal: '内存演示目标',
        chapter_range: [1, 3],
        planned_count: 3,
        draft_count: server.chapters.length,
        finalized_count: server.j?.applied.length ?? 0,
        chapters: [],
      },
    ],
    jobs: server.j && server.j.book === book ? [copy(server.j)] : [],
    warnings: [],
  })
  const review = () => ({
    job: copy(server.j),
    candidate: server.candidate,
    preview_digest: server.digest,
    files: [{ path: 'OUTLINE.md', before: '旧', after: '新' }],
    chapters: copy(server.chapters),
    warnings: [],
  })
  const handlers = {
    volumes: async (book) => volumes(book),
    job: async () => copy(server.j),
    review: async () => review(),
    create: async (book, request) => {
      server.j = mkJob(book, 'preparing', { request })
      server.candidate = null
      return copy(server.j)
    },
    candidate: async (_book, _id, candidate) => {
      server.candidate = candidate
      server.digest = 'digest-v2'
      server.j.etag = 'job-v2'
      return review()
    },
    confirm: async () => {
      server.j.status = 'generating'
      server.j.phase = 'prose'
      server.j.etag = 'job-v2'
      server.candidate = null
      return copy(server.j)
    },
    pause: async () => {
      server.j.status = 'paused'
      server.j.reason = 'M-04 命中：转人工，禁止自动改写'
      server.j.etag = 'job-paused'
      return copy(server.j)
    },
    resume: async () => {
      server.j.status = 'generating'
      server.j.etag = 'job-resumed'
      return copy(server.j)
    },
    cancel: async () => {
      server.j.status = 'cancelled'
      server.j.etag = 'job-cancelled'
      return copy(server.j)
    },
    finalize: async (_book, _id, request) => {
      server.j.applied = request.chapter_nos
      server.j.status = request.chapter_nos.length === 3 ? 'applied' : 'partially_applied'
      server.j.etag = 'job-final'
      return {
        job: copy(server.j),
        results: request.chapter_nos.map((no) => ({ no, applied: true })),
        stopped: false,
      }
    },
    ...overrides,
  }
  const api = Object.fromEntries(
    Object.entries(handlers).map(([name, fn]) => [
      name,
      async (...args) => {
        calls.push([name, copy(args)])
        return fn(...args)
      },
    ]),
  )
  return { server, api, calls, volumes, review }
}
function harness(
  mock,
  component = null,
  props = {},
  confirm = async () => 'confirm',
  route = vue.reactive({ params: { name: '离线书' }, query: {} }),
) {
  const timers = clock(),
    hooks = [],
    events = [],
    messages = []
  const fakeVue = { ...vue, onUnmounted: (fn) => hooks.push(fn) }
  const imports = {
    vue: fakeVue,
    '@/api/production': { productionApi: mock.api },
    '@/utils/production': utils,
    '@/utils/errors': errors,
  }
  const composable = evaluate(source('composables/useVolumeProduction.ts'), imports, timers)
  Object.assign(imports, {
    '@/composables/useVolumeProduction': composable,
    'vue-router': {
      useRoute: () => route,
      useRouter: () => ({ push: (p) => events.push(['navigate', p]) }),
    },
    'element-plus': {
      ElMessage: Object.fromEntries(
        ['info', 'warning', 'success', 'error'].map((kind) => [
          kind,
          (message) => messages.push([kind, message]),
        ]),
      ),
      ElMessageBox: { confirm },
    },
    '@/stores/book': { useBookStore: () => ({ setCurrentBook() {} }) },
    '@/utils/editing': { registerDirtyGuard: () => () => {} },
    '@/utils/prose': prose,
    '@/utils/tasks': { tasks: [] },
    '@/utils/status': {
      statusLabel: (s) => ({ pending_finalize: '待定稿', finalized: '已定稿' })[s] ?? s,
    },
  })
  const scope = vue.effectScope()
  let state, render
  if (component) {
    const filename = `${component}.vue`,
      { descriptor, errors } = parse(source(filename), { filename })
    assert.deepEqual(errors, [])
    const script = compileScript(descriptor, { id: 'spec10' })
    const template = compileTemplate({
      source: descriptor.template.content,
      filename,
      id: 'spec10',
    })
    assert.deepEqual(template.errors, [])
    const compiled = evaluate(script.content, imports, timers).default
    state = scope.run(() =>
      compiled.setup(props, { expose() {}, emit: (...args) => events.push(args) }),
    )
    // Node纯VNode渲染不在DOM实例内；指令行为另由真实浏览器核验，不调用Vue警告路径。
    render = evaluate(
      template.code,
      { vue: { ...vue, resolveComponent: (name) => name, withDirectives: (vnode) => vnode } },
      timers,
    ).render
  } else
    state = scope.run(() => composable.useVolumeProduction(vue.computed(() => route.params.name)))
  return {
    state,
    timers,
    events,
    messages,
    route,
    text: () => textOf(render(vue.proxyRefs({ ...props, ...state }), [])),
    unmount() {
      hooks.forEach((fn) => fn())
      scope.stop()
    },
  }
}
function textOf(node) {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(textOf).join('')
  if (node?.children && typeof node.children === 'object' && !Array.isArray(node.children))
    return Object.values(node.children)
      .filter((v) => typeof v === 'function')
      .map((fn) => textOf(fn()))
      .join('')
  return textOf(node?.children ?? '')
}
const writes = (mock) => mock.calls.filter(([name]) => !['volumes', 'job', 'review'].includes(name))

test('准备/页面读取不会自动 confirm 或 finalize，刷新只找回服务端任务', async () => {
  const mock = mockServer(),
    h = harness(mock)
  try {
    await flush()
    assert.equal(h.state.job.value.status, 'awaiting_outline_confirmation')
    assert.equal(h.timers.pending.size, 0)
    await h.state.refresh()
    assert.deepEqual(writes(mock), [])
    assert.equal(h.state.job.value.id, mock.server.j.id)
  } finally {
    h.unmount()
  }
})
test('真实 VolumeView 二次弹窗取消零请求、重复点确认只有一个弹窗和一个人工 confirm', async () => {
  const mock = mockServer(),
    dialog = deferred()
  let dialogs = 0
  const h = harness(mock, 'views/VolumeView', {}, () => {
    dialogs++
    return dialog.promise
  })
  try {
    await flush()
    h.state.checked.value = true
    const first = h.state.confirmVolume()
    await h.state.confirmVolume()
    assert.equal(dialogs, 1)
    assert.deepEqual(writes(mock), [])
    dialog.resolve('confirm')
    await first
    await flush()
    assert.equal(writes(mock).length, 1)
    const [name, args] = writes(mock)[0]
    assert.equal(name, 'confirm')
    assert.equal(args[0], '离线书')
    assert.equal(args[2], 'digest-v1')
    assert.equal(args[4], 'job-v1')
    assert.match(args[3], /^[a-f0-9-]{36}$/)
    assert.equal(h.state.job.value.status, 'generating')
    assert.equal(h.timers.pending.size, 1)
    assert.ok(!mock.calls.some(([name]) => name === 'finalize'))
  } finally {
    h.unmount()
  }
  const cancelMock = mockServer(),
    c = harness(cancelMock, 'views/VolumeView', {}, async () => {
      throw 'cancel'
    })
  try {
    await flush()
    c.state.checked.value = true
    await c.state.confirmVolume()
    assert.deepEqual(writes(cancelMock), [])
  } finally {
    c.unmount()
  }
})
test('每1000ms服务端phase轮询，进入等待作者审核即停止；卸载不cancel后台', async () => {
  const mock = mockServer(mkJob('离线书', 'generating')),
    h = harness(mock)
  mock.server.candidate = null
  try {
    await flush()
    assert.equal(h.timers.pending.size, 1)
    mock.server.j.phase = 'mech_check'
    mock.server.j.current_chapter = 2
    mock.server.j.completed = [1]
    await h.timers.tick()
    assert.equal(h.state.job.value.phase, 'mech_check')
    assert.equal(h.state.job.value.current_chapter, 2)
    mock.server.j.status = 'awaiting_final_review'
    mock.server.j.completed = [1, 2, 3]
    mock.server.chapters = [1, 2, 3].map(mkChapter)
    await h.timers.tick()
    assert.equal(h.state.review.value.chapters.length, 3)
    assert.equal(h.timers.pending.size, 0)
    assert.deepEqual(writes(mock), [])
    h.unmount()
    assert.equal(h.timers.pending.size, 0)
    assert.ok(!mock.calls.some(([name]) => name === 'cancel'))
  } finally {
    h.unmount()
  }
})
test('跨书晚到 GET/写响应不会串写新书状态或候选', async () => {
  const late = deferred(),
    route = vue.reactive({ params: { name: '书A' }, query: {} })
  const mock = mockServer(mkJob('书A'), {
    volumes: async (book) =>
      book === '书A'
        ? late.promise
        : { book, title: book, etag: 'b', volumes: [], jobs: [], warnings: [] },
  })
  const h = harness(mock, null, {}, undefined, route)
  try {
    route.params.name = '书B'
    await flush()
    late.resolve(mock.volumes('书A'))
    await flush()
    assert.equal(h.state.model.value.book, '书B')
    assert.equal(h.state.job.value, null)
    assert.equal(h.state.candidateText.value, '')
  } finally {
    h.unmount()
  }
  const response = deferred(),
    m = mockServer(mkJob('书A'), { confirm: async () => response.promise }),
    w = harness(m, null, {}, undefined, vue.reactive({ params: { name: '书A' }, query: {} }))
  try {
    await flush()
    w.state.checkedDigest.value = 'digest-v1'
    const pending = w.state.confirm()
    w.route.params.name = '书B'
    await flush()
    response.resolve(mkJob('书A', 'generating'))
    await pending
    assert.equal(w.state.model.value.book, '书B')
    assert.equal(w.state.job.value, null)
  } finally {
    w.unmount()
  }
})
test('候选编辑即撤销核对，PUT更新preview_digest/ETag，保存不是confirm', async () => {
  const mock = mockServer(),
    h = harness(mock)
  try {
    await flush()
    h.state.checkedDigest.value = 'digest-v1'
    assert.equal(h.state.confirmReady.value, true)
    const edited = mkBundle()
    edited.outlines[0].body = '作者改动'
    h.state.candidateText.value = JSON.stringify(edited)
    await flush()
    assert.equal(h.state.confirmReady.value, false)
    assert.equal(await h.state.confirm(), false)
    assert.equal(await h.state.saveCandidate(), true)
    await flush()
    assert.equal(h.state.review.value.preview_digest, 'digest-v2')
    assert.equal(h.state.job.value.etag, 'job-v2')
    assert.equal(h.state.candidateDirty.value, false)
    assert.equal(h.state.confirmReady.value, false)
    assert.deepEqual(
      writes(mock).map(([name]) => name),
      ['candidate'],
    )
    assert.deepEqual(mock.server.candidate.outlines[0].fm.extra, { 保留: true })
  } finally {
    h.unmount()
  }
})
test('candidate 412/409保留编辑文本、旧基线及输入；刷新后仍须保存和重新核对', async () => {
  for (const status of [412, 409]) {
    const mock = mockServer(undefined, {
        candidate: async () => {
          throw {
            response: {
              status,
              data: { detail: { code: 'PRODUCTION_STALE', message: '输入版本变化' } },
            },
          }
        },
      }),
      h = harness(mock)
    try {
      await flush()
      const baseline = h.state.baseline.value
      const edited = mkBundle()
      edited.notes.push('本地输入')
      h.state.candidateText.value = JSON.stringify(edited)
      h.state.form.requirements = '作者要求'
      await flush()
      const text = h.state.candidateText.value
      assert.equal(await h.state.saveCandidate(), false)
      assert.equal(h.state.conflict.value, true)
      assert.equal(h.state.candidateText.value, text)
      assert.equal(h.state.baseline.value, baseline)
      assert.equal(h.state.form.requirements, '作者要求')
      mock.server.j.etag = 'job-current'
      await h.state.refresh()
      assert.equal(h.state.candidateText.value, text)
      assert.equal(h.state.candidateDirty.value, true)
      assert.equal(h.state.confirmReady.value, false)
      assert.equal(h.state.job.value.etag, 'job-current')
    } finally {
      h.unmount()
    }
  }
})
test('暂停原因来自服务端，resume显式发新ID、不自动越过红线', async () => {
  const mock = mockServer(mkJob('离线书', 'generating')),
    h = harness(mock)
  try {
    await flush()
    await h.state.control('pause')
    assert.match(h.state.job.value.reason, /M-04/)
    assert.equal(h.timers.pending.size, 0)
    assert.match(h.state.notice.value, /计费/)
    assert.deepEqual(
      writes(mock).map(([n]) => n),
      ['pause'],
    )
    await h.state.control('resume')
    assert.deepEqual(
      writes(mock).map(([n]) => n),
      ['pause', 'resume'],
    )
    assert.match(writes(mock)[1][1][2], /^[a-f0-9-]{36}$/)
    assert.equal(h.state.job.value.status, 'generating')
  } finally {
    h.unmount()
  }
})
test('缺关键字段不是空任务，未知状态只读，未知候选版本禁止确认，计数未知不当零', async () => {
  const mock = mockServer(undefined, {
      volumes: async () => ({ book: '离线书', title: '离线书', etag: 'v', volumes: [] }),
    }),
    h = harness(mock)
  try {
    await flush()
    assert.equal(h.state.invalidData.value, true)
    assert.equal(h.state.model.value, null)
    assert.equal(await h.state.prepare(), false)
    assert.deepEqual(writes(mock), [])
  } finally {
    h.unmount()
  }
  assert.throws(
    () => utils.parseBundle(JSON.stringify({ ...mkBundle(), schema_version: 2 })),
    /结构未知/,
  )
  assert.match(utils.jobLabel('future'), /未知任务状态/)
  assert.match(utils.checkText({ verdict: 'future' }), /未知结论/)
  const unknown = mockServer(mkJob('离线书', 'future')),
    u = harness(unknown)
  try {
    await flush()
    assert.equal(u.state.writeBlocked.value, true)
    assert.equal(await u.state.control('resume'), false)
    assert.deepEqual(writes(unknown), [])
  } finally {
    u.unmount()
  }
  const counts = mockServer().volumes('离线书')
  delete counts.volumes[0].planned_count
  utils.assertVolumes(counts, '离线书')
  assert.equal(counts.volumes[0].planned_count, null)
})
test('1—50整数边界/default10，prepare/append/new_volume严格分开，不挪当前卷选择', async () => {
  assert.deepEqual([0, 1, 10, 50, 51, 1.5, '', NaN, Infinity].map(utils.countValid), [
    false,
    true,
    true,
    true,
    false,
    false,
    false,
    false,
    false,
  ])
  for (const mode of ['prepare', 'append', 'new_volume']) {
    const mock = mockServer(null),
      h = harness(mock)
    try {
      await flush()
      assert.equal(h.state.form.count, 10)
      h.state.form.mode = mode
      h.state.form.logline = '离线输入'
      h.state.form.count = 51
      assert.equal(await h.state.prepare(), false)
      assert.deepEqual(writes(mock), [])
      h.state.form.count = 1
      await h.state.prepare()
      const request = writes(mock)[0][1][1]
      assert.equal(request.mode, mode)
      assert.equal(request.count, 1)
      assert.equal(request.volume_no, mode === 'new_volume' ? undefined : 1)
      assert.equal(h.state.volumeNo.value, 1)
      assert.ok(!('auto_finalize' in request))
    } finally {
      h.unmount()
    }
  }
})
test('真实审稿SFC不自动通读/定稿；仅已读连续段二次确认后携本次各章指纹发送', async () => {
  const mock = mockServer(mkJob('离线书', 'awaiting_final_review', { completed: [1, 2, 3] }))
  mock.server.candidate = null
  mock.server.chapters = [1, 2, 3].map(mkChapter)
  const props = vue.reactive({ review: mock.review(), chapterNo: 1, disabled: false, busy: false }),
    dialog = deferred()
  let count = 0
  const h = harness(mock, 'components/VolumeReviewPanel', props, () => {
    count++
    return dialog.promise
  })
  try {
    await flush()
    assert.deepEqual(h.state.read.value, {})
    assert.deepEqual(h.events, [])
    assert.match(h.text(), /机检：通过/)
    assert.equal(h.state.mapping.value.paragraphs[1].startLine, 4)
    assert.equal(props.review.chapters[0].body.includes('\r\n'), true)
    h.state.markRead()
    h.state.choose(1, true)
    props.chapterNo = 3
    await flush()
    h.state.markRead()
    h.state.choose(3, true)
    assert.equal(h.state.ready.value, false)
    props.chapterNo = 2
    await flush()
    h.state.markRead()
    h.state.choose(2, true)
    assert.equal(h.state.ready.value, true)
    const request = h.state.requestFinalize()
    await h.state.requestFinalize()
    assert.equal(count, 1)
    assert.deepEqual(h.events, [])
    dialog.resolve('confirm')
    await request
    const event = h.events.find(([name]) => name === 'finalize')
    assert.deepEqual(event, ['finalize', [1, 2, 3], { 1: 'fp-1', 2: 'fp-2', 3: 'fp-3' }])
    const p = harness(mock)
    try {
      await flush()
      assert.equal(await p.state.finalize(event[1], event[2]), true)
      const payload = writes(mock).at(-1)[1][2]
      assert.equal(payload.confirmed, true)
      assert.deepEqual(payload.chapter_nos, [1, 2, 3])
      assert.deepEqual(payload.fingerprints, event[2])
      assert.ok(payload.client_request_id)
      assert.ok(!('force' in payload))
    } finally {
      p.unmount()
    }
  } finally {
    h.unmount()
  }
})
test('改稿后通读指纹失效；定稿取消、过期、缺检查、非连续章段均不提交', async () => {
  const mock = mockServer(mkJob('离线书', 'awaiting_final_review'))
  mock.server.candidate = null
  mock.server.chapters = [1, 2, 3].map(mkChapter)
  const props = vue.reactive({ review: mock.review(), chapterNo: 1, disabled: false, busy: false }),
    h = harness(mock, 'components/VolumeReviewPanel', props, async () => {
      throw 'cancel'
    })
  try {
    await flush()
    h.state.markRead()
    h.state.choose(1, true)
    await h.state.requestFinalize()
    assert.deepEqual(h.events, [])
    props.review.chapters[0].fingerprint = 'changed'
    await flush()
    assert.deepEqual(h.state.read.value, {})
    assert.deepEqual(h.state.selected.value, [])
    assert.equal(h.state.ready.value, false)
  } finally {
    h.unmount()
  }
  const p = harness(mock)
  try {
    await flush()
    assert.equal(await p.state.finalize([1, 3], { 1: 'fp-1', 3: 'fp-3' }), false)
    mock.server.chapters[0].check = null
    await p.state.refresh()
    assert.equal(await p.state.finalize([1], { 1: 'fp-1' }), false)
    assert.deepEqual(writes(mock), [])
  } finally {
    p.unmount()
  }
})
test('写请求网络未知不自动重发，刷新后同内容沿用client_request_id，重点击禁重', async () => {
  const wait = deferred(),
    mock = mockServer(null, { create: async () => wait.promise }),
    h = harness(mock)
  try {
    await flush()
    h.state.form.logline = '保留输入'
    const first = h.state.prepare()
    assert.equal(await h.state.prepare(), false)
    assert.equal(writes(mock).length, 1)
    wait.reject(new Error('断线'))
    await first
    assert.equal(h.state.uncertain.value, true)
    assert.equal(h.state.form.logline, '保留输入')
    await h.state.prepare()
    assert.equal(writes(mock).length, 1)
    await h.state.refresh()
    await h.state.prepare()
    assert.equal(writes(mock).length, 2)
    assert.equal(writes(mock)[0][1][1].client_request_id, writes(mock)[1][1][1].client_request_id)
  } finally {
    h.unmount()
  }
})
test('部分定稿回执停止保留成功章，不自动重试或伪称整卷定稿', async () => {
  const mock = mockServer(mkJob('离线书', 'awaiting_final_review'), {
    finalize: async () => ({
      job: mkJob('离线书', 'partially_applied', { completed: [1, 2, 3], applied: [1] }),
      results: [
        { no: 1, applied: true },
        { no: 2, applied: false, error: '写回冲突' },
      ],
      stopped: true,
    }),
  })
  mock.server.candidate = null
  mock.server.chapters = [1, 2, 3].map(mkChapter)
  const h = harness(mock)
  try {
    await flush()
    await h.state.finalize([1, 2], { 1: 'fp-1', 2: 'fp-2' })
    assert.equal(h.state.receipt.value.stopped, true)
    assert.match(h.state.notice.value, /已停止/)
    assert.equal(writes(mock).length, 1)
  } finally {
    h.unmount()
  }
})
test('真实production API精确路径/If-Match/payload，无旧端点或额外人工请求', async () => {
  const calls = [],
    http = Object.fromEntries(
      ['get', 'post', 'put'].map((verb) => [
        verb,
        async (...args) => {
          calls.push([verb, ...args])
          return { data: {} }
        },
      ]),
    )
  const api = evaluate(source('api/production.ts'), { './client': { http } }).productionApi
  await api.volumes('含空格 书')
  await api.job('含空格 书', 'id')
  await api.review('含空格 书', 'id')
  await api.create(
    '含空格 书',
    { mode: 'prepare', count: 10, logline: '', requirements: '', client_request_id: 'uuid' },
    'book-etag',
  )
  await api.candidate('含空格 书', 'id', mkBundle(), 'job-etag')
  await api.confirm('含空格 书', 'id', 'digest', 'uuid', 'job-etag')
  await api.pause('含空格 书', 'id', 'job-etag')
  await api.cancel('含空格 书', 'id', 'job-etag')
  await api.resume('含空格 书', 'id', 'uuid', 'job-etag')
  await api.finalize(
    '含空格 书',
    'id',
    { chapter_nos: [1], fingerprints: { 1: 'fp' }, client_request_id: 'uuid', confirmed: true },
    'job-etag',
  )
  assert.deepEqual(
    calls.map(([verb, path]) => [
      verb,
      path.slice(
        path.indexOf('/volumes') >= 0 ? path.indexOf('/volumes') : path.indexOf('/production'),
      ),
    ]),
    [
      ['get', '/volumes'],
      ['get', '/production/id'],
      ['get', '/production/id/review'],
      ['post', '/production'],
      ['put', '/production/id/candidate'],
      ['post', '/production/id/confirm'],
      ['post', '/production/id/pause'],
      ['post', '/production/id/cancel'],
      ['post', '/production/id/resume'],
      ['post', '/production/id/finalize'],
    ],
  )
  assert.ok(calls.every(([, path]) => path.includes(encodeURIComponent('含空格 书'))))
  assert.deepEqual(calls[5][2], { preview_digest: 'digest', client_request_id: 'uuid' })
  assert.deepEqual(calls[6][2], {})
  assert.deepEqual(calls[7][2], {})
  assert.deepEqual(calls[8][2], { client_request_id: 'uuid' })
  assert.ok(
    calls
      .slice(3)
      .every(
        (entry) =>
          entry[3].headers['If-Match'] ===
          (entry[1].endsWith('/production') ? 'book-etag' : 'job-etag'),
      ),
  )
})
test('真实候选/进度SFC可编译渲染中文范围、编辑失效、暂停原因与未知状态', async () => {
  const mock = mockServer(
    mkJob('离线书', 'paused', { reason: '服务端暂停原因', current_chapter: 2, completed: [1] }),
  )
  const progress = harness(
    mock,
    'components/ProductionProgress',
    vue.reactive({ job: mock.server.j, disabled: false, busy: '' }),
  )
  try {
    assert.match(progress.text(), /服务端暂停原因/)
    assert.match(progress.text(), /第 2 章/)
    assert.match(progress.text(), /尚未完成检查 \/ 未执行/)
    assert.match(progress.text(), /已发出的模型请求可能继续完成及计费/)
  } finally {
    progress.unmount()
  }
  const plan = harness(
    mock,
    'components/VolumePlanPreview',
    vue.reactive({
      review: mock.review(),
      modelValue: JSON.stringify(mkBundle()),
      dirty: true,
      checked: false,
      disabled: false,
      saving: false,
      confirmReady: false,
    }),
  )
  try {
    assert.match(plan.text(), /候选已修改/)
    assert.match(plan.text(), /旧预览/)
    assert.match(plan.text(), /确认本卷全部细纲并开始生成/)
  } finally {
    plan.unmount()
  }
})
test('空书创建准备任务后，人工确认发布卷时自动定位实际卷，不显示空书标题', async () => {
  const mock = mockServer(null)
  const originalVolumes = mock.api.volumes
  mock.api.volumes = async (book) =>
    mock.server.j?.status === 'generating'
      ? originalVolumes(book)
      : {
          book,
          title: book,
          etag: 'book-v1',
          volumes: [],
          jobs: mock.server.j ? [copy(mock.server.j)] : [],
          warnings: [],
        }
  const h = harness(mock, 'views/VolumeView')
  try {
    await flush()
    assert.equal(h.state.volumeNo.value, null)
    h.state.form.logline = '离线输入'
    await h.state.production.prepare()
    mock.server.j.status = 'awaiting_outline_confirmation'
    mock.server.candidate = mkBundle()
    await h.state.production.refresh()
    h.state.checked.value = true
    await h.state.confirmVolume()
    await flush()
    assert.equal(h.state.volumeNo.value, 1)
    assert.match(h.state.mainTitle.value, /第 1 卷/)
  } finally {
    h.unmount()
  }
})
