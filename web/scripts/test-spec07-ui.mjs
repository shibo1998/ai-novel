// 规格07：在 Node 中编译并执行真实 TS/SFC setup；不启动服务、不访问书稿、不调用模型。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const { compileScript, parse } = require('@vue/compiler-sfc')
const ts = require('typescript')
const vue = require('vue')

const memory = new Map()
globalThis.localStorage = {
  getItem: (key) => memory.get(key) ?? null,
  setItem: (key, value) => memory.set(key, String(value)),
  removeItem: (key) => memory.delete(key),
}
globalThis.document = { querySelector: () => null }

const flush = async () => {
  await vue.nextTick()
  await new Promise((resolve) => setImmediate(resolve))
  await vue.nextTick()
}
function deferred() {
  let resolve
  let reject
  const promise = new Promise((done, fail) => {
    resolve = done
    reject = fail
  })
  return { promise, resolve, reject }
}
function transpile(source) {
  return ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
}
function messages() {
  const entries = []
  const log = (kind) => (value) => {
    entries.push([kind, value])
    return { close() {} }
  }
  return {
    entries,
    ElMessage: {
      success: log('success'),
      warning: log('warning'),
      error: log('error'),
      info: log('info'),
    },
  }
}
function loadTs(relative, imports = {}) {
  const filename = fileURLToPath(new URL(relative, import.meta.url))
  const js = transpile(readFileSync(filename, 'utf8'))
  const module = { exports: {} }
  const msg = messages()
  new Function('require', 'module', 'exports', js)(
    (name) => {
      if (name in imports) return imports[name]
      if (name === 'vue') return vue
      if (name === 'element-plus') return { ElMessage: msg.ElMessage }
      if (name.startsWith('.'))
        return loadTs(new URL(`${name}.ts`, new URL(relative, import.meta.url)).href, imports)
      throw new Error(`未模拟的 TS 依赖：${name}`)
    },
    module,
    module.exports,
  )
  return { ...module.exports, messages: msg.entries }
}
function setupSfc(relative, props, imports, confirm = async () => 'confirm') {
  const filename = fileURLToPath(new URL(relative, import.meta.url))
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
  const compiled = compileScript(descriptor, {
    id: `spec07-${Math.random().toString(36).slice(2)}`,
  })
  const js = transpile(compiled.content)
  const mounted = []
  const unmounted = []
  const events = []
  const msg = messages()
  const module = { exports: {} }
  const defaults = {
    vue: {
      ...vue,
      onMounted: (hook) => mounted.push(hook),
      onUnmounted: (hook) => unmounted.push(hook),
    },
    axios: { isAxiosError: (error) => Boolean(error?.isAxiosError) },
    'element-plus': {
      ElMessage: msg.ElMessage,
      ElMessageBox: { confirm, prompt: async () => ({ value: '测试原因' }) },
    },
  }
  new Function('require', 'module', 'exports', js)(
    (name) => {
      if (name in imports) return imports[name]
      if (name in defaults) return defaults[name]
      if (name.endsWith('.vue')) return {}
      throw new Error(`未模拟的 SFC 依赖：${name}`)
    },
    module,
    module.exports,
  )
  const scope = vue.effectScope()
  const state = scope.run(() =>
    module.exports.default.setup(props, {
      expose() {},
      emit: (...args) => events.push(args),
    }),
  )
  for (const hook of mounted) hook()
  return {
    state,
    events,
    messages: msg.entries,
    unmount() {
      for (const hook of unmounted) hook()
      scope.stop()
    },
  }
}

const editing = loadTs('../src/utils/editing.ts')
const errorUtils = loadTs('../src/utils/errors.ts')
const taskUtils = loadTs('../src/utils/tasks.ts', { './errors': errorUtils })
const streamUtils = loadTs('../src/utils/stream.ts')
const proseUtils = loadTs('../src/utils/prose.ts')
const conflictUtils = loadTs('../src/utils/conflicts.ts')
const statusUtils = {
  levelLabel: String,
  ruleLabel: String,
  stageLabel: String,
  statusLabel: String,
  tagType: () => 'info',
  verdictLabel: String,
}

function settingsHarness(getLint, putLint, confirm) {
  return setupSfc(
    '../src/views/SettingsView.vue',
    {},
    {
      '@/api': {
        getSettings: async () => ({}),
        getFtsMode: async () => ({ mode: 'test' }),
        getLint,
        putLint,
        putSettings: async () => ({}),
        rebuildDb: async () => ({}),
        testConnection: async () => ({ ok: true }),
      },
      '@/utils/editing': editing,
      '@/utils/tasks': { hasPendingWrite: () => false },
    },
    confirm,
  )
}
const versioned = (content, etag) => ({ data: { content, exists: true }, etag })

test('词库 A/B 响应倒序时，只允许最后目标的响应更新编辑会话', async () => {
  const a = deferred()
  const b = deferred()
  const component = settingsHarness(
    (which) => (which === 'banned_words' ? a.promise : b.promise),
    async () => ({}),
  )
  try {
    await flush()
    const switching = component.state.switchLint('sensitive_words')
    await flush()
    b.resolve(versioned('B 内容', 'etag-b'))
    await switching
    a.resolve(versioned('A 迟到内容', 'etag-a'))
    await flush()
    assert.equal(component.state.lintWhich.value, 'sensitive_words')
    assert.equal(component.state.loadedWhich.value, 'sensitive_words')
    assert.equal(component.state.lintText.value, 'B 内容')
    assert.equal(component.state.lintEtag.value, 'etag-b')
  } finally {
    component.unmount()
  }
})

test('脏词库切换支持取消、保存后继续；保存仍绑定原资源和原 ETag', async () => {
  let mode = 'close'
  const puts = []
  const component = settingsHarness(
    async (which) => versioned(which === 'banned_words' ? 'A 原文' : 'B 原文', `etag-${which}`),
    async (...args) => {
      puts.push(args)
      return {}
    },
    async () => (mode === 'save' ? 'confirm' : Promise.reject(mode)),
  )
  try {
    await flush()
    component.state.lintText.value = '未保存草稿'
    await component.state.switchLint('sensitive_words')
    assert.equal(component.state.lintWhich.value, 'banned_words')
    assert.equal(component.state.lintText.value, '未保存草稿')
    mode = 'save'
    await component.state.switchLint('sensitive_words')
    assert.deepEqual(puts, [['banned_words', '未保存草稿', 'etag-banned_words']])
    assert.equal(component.state.lintWhich.value, 'sensitive_words')
    assert.equal(component.state.lintText.value, 'B 原文')
  } finally {
    component.unmount()
  }
})

test('词库 412 冲突保留草稿、原基线和 ETag，不刷新覆盖输入', async () => {
  const component = settingsHarness(
    async () => versioned('服务器原文', 'etag-old'),
    async () => {
      throw { response: { status: 412 } }
    },
  )
  try {
    await flush()
    component.state.lintText.value = '我的冲突草稿'
    assert.equal(await component.state.saveLint(), false)
    assert.equal(component.state.lintText.value, '我的冲突草稿')
    assert.equal(component.state.lintBaseline.value, '服务器原文')
    assert.equal(component.state.lintEtag.value, 'etag-old')
    assert.equal(component.state.lintDirty.value, true)
  } finally {
    component.unmount()
  }
})

function archiveApi(bookData, overrides = {}) {
  return {
    getMetaVersioned: async (book) => ({
      data: { title: bookData[book]?.title ?? book },
      etag: `meta-${book}`,
    }),
    getBookConfigVersioned: async () => ({ data: {}, etag: 'config-v1' }),
    getBibleVersioned: async () => ({ data: { 基础设定: '圣经原文' }, etag: 'bible-v1' }),
    listCharactersVersioned: async () => ({ data: [], etag: 'characters-v1' }),
    getLocationsVersioned: async () => ({ data: [], etag: 'locations-v1' }),
    getPlotPointsVersioned: async () => ({ data: [], etag: 'plot-v1' }),
    getOutlineVersioned: async () => ({
      data: { chapter_rows: [{ 章: '1', 本章目标: '开篇' }] },
      etag: 'outline-v1',
    }),
    getCharacterVersioned: async () => ({ data: { _body: '' }, etag: 'character-v1' }),
    putMeta: async (_book, payload) => payload,
    putBookConfig: async (_book, payload) => payload,
    putBible: async () => ({}),
    putCharacter: async () => ({}),
    putLocations: async () => ({}),
    putPlotPoints: async () => ({}),
    putOutlineFull: async () => ({}),
    createCharacter: async () => ({}),
    ...overrides,
  }
}
function archiveHarness(route, api, guardBox) {
  return setupSfc(
    '../src/views/ArchiveView.vue',
    {},
    {
      '@/api': api,
      '@/api/types': { MODEL_STAGES: ['outline', 'prose', 'review', 'fix', 'polish'] },
      'vue-router': { useRoute: () => route, useRouter: () => ({ push: async () => {} }) },
      '@/stores/book': { useBookStore: () => ({ setCurrentBook() {} }) },
      '@/utils/editing': {
        registerDirtyGuard: (guard) => {
          guardBox.guard = guard
          return () => {}
        },
      },
      '@/utils/h2': loadTs('../src/utils/h2.ts'),
      '@/utils/mdtable': loadTs('../src/utils/mdtable.ts'),
      '@/utils/status': statusUtils,
    },
  )
}

test('档案 node query 初次定位与同页定位都加载真实目标；版本冲突保留文本', async () => {
  const route = vue.reactive({ params: { name: '书A' }, query: { node: 'outline:rows' } })
  const api = archiveApi(
    { 书A: {} },
    {
      putBible: async () => {
        throw { response: { status: 412 } }
      },
    },
  )
  const component = archiveHarness(route, api, {})
  try {
    await flush()
    assert.equal(component.state.selectedId.value, 'outline:rows')
    assert.equal(component.state.outlineRows.value.length, 1)
    route.query.node = 'bible:基础设定'
    await flush()
    assert.equal(component.state.selectedId.value, 'bible:基础设定')
    assert.equal(component.state.bibleText.value, '圣经原文')
    component.state.bibleText.value = '冲突时仍要保留的输入'
    assert.equal(await component.state.saveBibleSection(), false)
    assert.equal(component.state.bibleText.value, '冲突时仍要保留的输入')
  } finally {
    component.unmount()
  }
})

test('脏档案保存完成后才切书，请求和后续基线均属于各自书籍', async () => {
  const route = vue.reactive({ params: { name: '书A' }, query: {} })
  const puts = []
  const api = archiveApi(
    { 书A: { title: 'A 标题' }, 书B: { title: 'B 标题' } },
    {
      putMeta: async (book, payload, etag) => {
        puts.push([book, payload.title, etag])
        return payload
      },
    },
  )
  const guardBox = {}
  const component = archiveHarness(route, api, guardBox)
  try {
    await flush()
    component.state.meta.value.title = 'A 新标题'
    assert.equal(await guardBox.guard(), 'save')
    assert.deepEqual(puts, [['书A', 'A 新标题', 'meta-书A']])
    route.params.name = '书B'
    await flush()
    assert.equal(component.state.meta.value.title, 'B 标题')
    assert.equal(component.state.dirty.value, false)
  } finally {
    component.unmount()
  }
})

test('SSE 无 done/error 结束帧时保留尾字并进入结果待确认，核验后完成任务', async () => {
  taskUtils.resetTasksForTest()
  errorUtils.errorRecords.splice(0)
  const route = vue.reactive({ params: { name: '书A', no: '1' } })
  const workbench = { status: 'generating', no: 1, row: { state_revision: 1 }, prose: null }
  const frames = [new TextEncoder().encode('event: delta\ndata: {"text":"尾字"}\n\n')]
  let index = 0
  const previousFetch = globalThis.fetch
  globalThis.fetch = async () => ({
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () =>
          index < frames.length ? { value: frames[index++], done: false } : { done: true },
      }),
    },
  })
  const component = setupSfc(
    '../src/views/WorkbenchView.vue',
    {},
    {
      '@/api': {
        getWorkbench: async () => workbench,
        listVersions: async () => [],
        getCheckStatus: async () => null,
        getDraft: async () => null,
        getDeslop: async () => null,
      },
      'vue-router': { useRoute: () => route, useRouter: () => ({ push() {} }) },
      '@/stores/book': { useBookStore: () => ({ rememberChapter() {} }) },
      '@/utils/status': statusUtils,
      '@/utils/prose': proseUtils,
      '@/utils/stream': streamUtils,
      '@/utils/errors': errorUtils,
      '@/utils/tasks': taskUtils,
      '@/utils/editing': editing,
    },
  )
  try {
    await flush()
    await component.state.generate()
    assert.equal(component.state.streamText.value, '尾字')
    assert.equal(taskUtils.tasks[0].phase, 'unknown')
    assert.equal(taskUtils.tasks[0].write, true)
    const result = await taskUtils.confirmTask(taskUtils.tasks[0].id)
    assert.match(result, /已核验原目标/)
    assert.equal(taskUtils.tasks[0].phase, 'completed')
  } finally {
    component.unmount()
    taskUtils.resetTasksForTest()
    globalThis.fetch = previousFetch
  }
})

test('停止等待不会冒充服务端取消；原目标确认回调可完成未知任务', async () => {
  taskUtils.resetTasksForTest()
  try {
    const running = taskUtils.withTask(
      { operation: '测试写操作', book: '书A', write: true, confirm: async () => '原文件已核验' },
      (signal) =>
        new Promise((resolve, reject) => {
          signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true })
        }),
    )
    assert.equal(taskUtils.tasks[0].phase, 'running')
    taskUtils.stopWaiting(taskUtils.tasks[0].id)
    await assert.rejects(running)
    assert.equal(taskUtils.tasks[0].phase, 'unknown')
    assert.match(taskUtils.tasks[0].result, /后台可能继续/)
    assert.equal(await taskUtils.confirmTask(taskUtils.tasks[0].id), '原文件已核验')
    assert.equal(taskUtils.tasks[0].phase, 'completed')
  } finally {
    taskUtils.resetTasksForTest()
  }
})

test('后台同源同因轮询错误聚合且通知不刷屏，前台错误保持永久通知', () => {
  errorUtils.errorRecords.splice(0)
  errorUtils.messages.splice(0)
  const error = { code: 'ERR_NETWORK', message: 'offline' }
  for (let i = 0; i < 3; i++)
    errorUtils.recordError(error, { operation: '轮询状态', book: '书A', background: true })
  assert.equal(errorUtils.errorRecords.length, 1)
  assert.equal(errorUtils.errorRecords[0].count, 3)
  assert.equal(errorUtils.messages.length, 0)
  errorUtils.recordError(error, { operation: '轮询状态', book: '书A' })
  assert.equal(errorUtils.errorRecords[0].count, 4)
  assert.equal(errorUtils.messages.length, 1)
  assert.equal(errorUtils.messages[0][1].duration, 0)
})

test('正文段落保留原始行号，空行可定位，重复引用拒绝猜测', () => {
  const source = '第一段\r\n同段第二行\r\n\r\n第二段\r\n重复\r\n\r\n重复'
  const mapping = proseUtils.buildProseMapping(source)
  assert.equal(mapping.paragraphs.length, 3)
  assert.equal(mapping.paragraphs[0].startLine, 1)
  assert.equal(mapping.paragraphs[0].endLine, 2)
  assert.equal(mapping.paragraphs[1].startLine, 4)
  assert.equal(proseUtils.paragraphForLine(mapping, 3).startLine, 4)
  assert.equal(proseUtils.paragraphForLine(mapping, 6).startLine, 7)
  assert.equal(proseUtils.paragraphForUniqueQuote(mapping, source, '第二段').startLine, 4)
  assert.equal(proseUtils.paragraphForUniqueQuote(mapping, source, '重复'), null)
  assert.equal(source.includes('\r\n'), true)
})

test('流式缓冲 flush 提交全部尾字，48px 跟随边界明确', async () => {
  const commits = []
  const buffer = new streamUtils.StreamTextBuffer((text) => commits.push(text), 10_000)
  buffer.append('前')
  buffer.append('后')
  buffer.flush()
  assert.deepEqual(commits, ['前后'])
  assert.equal(buffer.text, '前后')
  buffer.dispose()
  assert.deepEqual(streamUtils.updateFollowState(51, 100, 200), {
    following: false,
    showReturn: true,
  })
  assert.deepEqual(streamUtils.updateFollowState(52, 100, 200), {
    following: true,
    showReturn: false,
  })
})

test('AI 参数取消只关闭表单，不发送生成请求', async () => {
  let generations = 0
  const archiveAI = loadTs('../src/utils/archiveAI.ts', {
    '@/api': {
      aiGenerate: async () => {
        generations++
        return { text: '', digest: '' }
      },
    },
  })
  const component = setupSfc(
    '../src/components/ArchiveAI.vue',
    { book: '书A' },
    {
      '@/api': {
        getMeta: async () => ({ genre: '玄幻' }),
        getBookConfig: async () => ({}),
        getBibleVersioned: async () => ({ data: {}, etag: 'bible' }),
        listCharactersVersioned: async () => ({ data: [], etag: 'chars' }),
        getOutlineVersioned: async () => ({ data: {}, etag: 'outline' }),
        adoptArchive: async () => ({}),
      },
      '@/utils/archiveAI': archiveAI,
      '@/utils/tasks': taskUtils,
      '@/utils/errors': errorUtils,
      '@/utils/mdtable': loadTs('../src/utils/mdtable.ts'),
    },
  )
  try {
    await component.state.open('G1')
    assert.equal(component.state.parameterOpen.value, true)
    component.state.parameterOpen.value = false
    await flush()
    assert.equal(generations, 0)
    assert.equal(component.state.candidate.value, null)
  } finally {
    component.unmount()
  }
})

test('批量网络未知立即中断；重试只含明确失败和未执行项，不重跑成功或未知项', async () => {
  const items = [1, 2, 3, 4].map((id) => ({
    id,
    book: '书A',
    kind: 'dead_reappear',
    detail: '',
    chapter_no: 1,
    old: '',
    new: '候选',
  }))
  const calls = []
  const results = await conflictUtils.runConflictBatch(items, 'adopt_ai', async (item) => {
    calls.push(item.id)
    if (item.id === 1) return { effect: 'applied' }
    if (item.id === 2) throw { response: { status: 500 }, message: '明确失败' }
    throw { code: 'ERR_NETWORK', message: '断线' }
  })
  assert.deepEqual(calls, [1, 2, 3])
  assert.deepEqual(
    results.map((r) => r.status),
    ['success', 'failed', 'unknown', 'skipped'],
  )
  const retryIds = results
    .filter((r) => r.status === 'failed' || r.status === 'skipped')
    .map((r) => r.id)
  assert.deepEqual(retryIds, [2, 4])
  const retried = []
  await conflictUtils.runConflictBatch(
    items.filter((item) => retryIds.includes(item.id)),
    'adopt_ai',
    async (item) => {
      retried.push(item.id)
      return { effect: 'applied' }
    },
  )
  assert.deepEqual(retried, [2, 4])
})
