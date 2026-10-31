// 规格08 阶段A 前端离线测试：G5 影响预览的中文摘要与失知(false→0)防回归。
// 不启动服务、不访问书稿、不调用模型；只编译真实 src/utils/archiveAI.ts 执行。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const require = createRequire(import.meta.url)
const ts = require('typescript')
const here = dirname(fileURLToPath(import.meta.url))

function loadArchiveAI() {
  const src = readFileSync(resolve(here, '../src/utils/archiveAI.ts'), 'utf8')
  const js = ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
  const module = { exports: {} }
  const stubRequire = (id) => {
    if (id === '@/api')
      return {
        aiGenerate: async () => {
          throw new Error('离线测试不得调用模型')
        },
      }
    if (id === '@/api/types') return {}
    return {}
  }
  new Function('require', 'exports', 'module', js)(stubRequire, module.exports, module)
  return module.exports
}

const ai = loadArchiveAI()

function preview(over = {}) {
  return {
    ok: true,
    old_readable: true,
    plot_readable: true,
    old_rows: [],
    new_rows: [],
    old_row_numbers: [1, 2, 3, 4],
    new_row_numbers: [1, 2, 3],
    added_numbers: [],
    removed_numbers: [4],
    changed_numbers: [2],
    written: { numbers: [1, 2, 3], total: 3, known: true },
    affected_numbers: [1, 2, 3],
    affected_total: 3,
    volumes: 1,
    chapter_rows: 3,
    foreshadows_added: 1,
    foreshadows_kept: 1,
    foreshadow_rows: 2,
    id_map: {},
    outline_text_old: '# 旧',
    outline_text_new: '# 新',
    plot_points_text_old: '# 旧台账',
    plot_points_text_new: '# 新台账',
    warnings: [],
    ...over,
  }
}

test('章号压缩：空集说"无"，连续段压成区间，离散点保留', () => {
  assert.equal(ai.compressNumbers([]), '无')
  assert.equal(ai.compressNumbers([7, 8, 9, 10, 11, 12]), '7-12')
  assert.equal(ai.compressNumbers([3, 1, 7, 8, 9]), '1、3、7-9')
  assert.equal(ai.compressNumbers([5]), '5')
})

test('影响摘要按真实正文文件章号计数，并说明正文不会被重写', () => {
  const text = ai.outlineImpactText(preview())
  assert.match(text, /已有正文 3 章/)
  assert.match(text, /命中 3 章/)
  assert.match(text, /1-3/)
  assert.match(text, /正文不会随大纲采纳自动重写/)
  assert.match(text, /新增 1 条、保留 1 条/)
  assert.match(text, /删除章号：4/)
  assert.match(text, /内容变化：2/)
})

test('正文不可判定时绝不说成 0 章', () => {
  const text = ai.outlineImpactText(
    preview({
      written: { numbers: [], total: null, known: false },
      affected_total: null,
      affected_numbers: [],
    }),
  )
  assert.match(text, /无法判定/)
  assert.doesNotMatch(text, /已有正文 0 章/)
  assert.doesNotMatch(text, /命中 0 章/)
})

test('旧档读不出来时明确列出警告，不静默当成空旧档', () => {
  const text = ai.outlineImpactText(
    preview({
      old_readable: false,
      plot_readable: false,
      warnings: ['OUTLINE.md 读取失败（OSError），旧章行与差异无法判定'],
    }),
  )
  assert.match(text, /旧 OUTLINE\.md 读取失败/)
  assert.match(text, /旧 PLOT_POINTS\.md 读取失败/)
  assert.match(text, /警告：OUTLINE\.md 读取失败/)
})

test('diff 文本包含伏笔台账；台账不可判定时显式标注而不是留空', () => {
  const withPlot = ai.outlineDiffText('# 大纲', '# 台账', '旧台账')
  assert.match(withPlot, /## 伏笔台账/)
  assert.match(withPlot, /# 台账/)
  const noPlot = ai.outlineDiffText('# 大纲', null, '旧台账')
  assert.match(noPlot, /旧台账：无法判定/)
})

// ---------------------------------------------------------------------------
// 阶段B 前端离线测试（规格08 §6.3 P-T17/P-T18 的纯函数面）：规划展示与默认批次。
// ---------------------------------------------------------------------------
function loadPlanningUtils() {
  const src = readFileSync(resolve(here, '../src/utils/planning.ts'), 'utf8')
  const js = ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
  const module = { exports: {} }
  const stubRequire = (id) => {
    if (id === '@/api/types') return {}
    return {}
  }
  new Function('require', 'exports', 'module', js)(stubRequire, module.exports, module)
  return module.exports
}

const pu = loadPlanningUtils()

test('层键与服务端 adoptions 键格式一致（规格08 §2.3）', () => {
  assert.equal(
    pu.planningNodeKey('global', { volume_no: null, unit_id: null, chapters: null }),
    'global',
  )
  assert.equal(
    pu.planningNodeKey('volume', { volume_no: 2, unit_id: null, chapters: null }),
    'volume:2',
  )
  assert.equal(
    pu.planningNodeKey('unit', { volume_no: 2, unit_id: 'U-abc', chapters: null }),
    'unit:U-abc',
  )
  assert.equal(
    pu.planningNodeKey('chapters', { volume_no: 1, unit_id: 'U-abc', chapters: [3, 12] }),
    'chapters:U-abc:3-12',
  )
})

test('下一批次默认取接下来最多 10 章，不跨单元（规格08 §3.3）', () => {
  assert.deepEqual(pu.planningNextBatch([1, 25], [1, 2, 3]), [4, 13])
  assert.deepEqual(pu.planningNextBatch([1, 8], []), [1, 8]) // 短单元一批完成
  assert.equal(pu.planningNextBatch([1, 5], [1, 2, 3, 4, 5]), null) // 全部覆盖
  assert.equal(pu.planningNextBatch(null, []), null) // 粗骨架无范围不猜批次
  assert.equal(pu.planningNextBatch([9, 3], []), null) // 非法范围不猜批次
})

test('影响摘要按作用域给中文且不虚称改动（chapters 报行数）', () => {
  const text = pu.planningImpactText({
    scope: 'chapters',
    target: { volume_no: 1, unit_id: 'U-x', chapters: [4, 13] },
    added_numbers: [4, 5],
    changed_numbers: [6],
  })
  assert.match(text, /章纲批次 4-13/)
  assert.match(text, /新增 2 行、改动 1 行/)
  assert.match(text, /批外章行与其他批次小节保留/)
  const globalText = pu.planningImpactText({
    scope: 'global',
    target: { volume_no: null, unit_id: null, chapters: null },
    volumes_added: [3],
    volumes_touched: [1, 2],
  })
  assert.match(globalText, /新增卷 3/)
  assert.match(globalText, /正文不重写/)
})

test('就绪状态映射齐全：五态标签与 el-tag 类型（不把 invalid 当 missing）', () => {
  for (const status of ['missing', 'unadopted', 'ready', 'stale', 'invalid']) {
    assert.ok(pu.PLANNING_STATUS_LABEL[status], `${status} 有中文标签`)
    assert.ok(pu.PLANNING_STATUS_TAG[status], `${status} 有 tag 类型`)
  }
  assert.equal(pu.PLANNING_STATUS_TAG.invalid, 'danger')
  assert.equal(pu.PLANNING_STATUS_TAG.ready, 'success')
})

test('工作台规划门禁文案：legacy 放行为空串，未就绪展示服务端消息', () => {
  assert.equal(pu.planningGateText({ allowed: true, code: null, message: 'legacy' }), '')
  assert.equal(
    pu.planningGateText({
      allowed: false,
      code: 'PLANNING_NOT_READY',
      message: '全书粗纲尚未采纳',
    }),
    '全书粗纲尚未采纳',
  )
  assert.equal(pu.planningGateText(undefined), '')
})
