// 规格10 §3/5：读模型守卫与中文展示。未知字段保留，未知版本/缺关键字段禁止写入。
import type {
  ProductionBundle,
  ProductionJob,
  ProductionReview,
  ReviewChapter,
  VolumesResponse,
} from '@/api/productionTypes'

export const JOB_LABELS: Record<string, string> = {
  preparing: '准备整卷候选',
  awaiting_outline_confirmation: '等待整卷细纲确认',
  generating: '自动出稿与检查',
  awaiting_final_review: '等待作者通读定稿',
  applied: '本批已人工定稿',
  partially_applied: '本批部分定稿',
  paused: '已暂停',
  error: '任务出错',
  interrupted: '运行中断，待显式恢复',
  cancelled: '已停止后续生产',
  stale: '输入已变更，待复核',
  recovery_required: '提交待修复',
}
export const PHASE_LABELS: Record<string, string> = {
  queued: '排队中',
  preparing: '准备候选',
  planning: '组织卷规划',
  archives: '准备缺失档案',
  outline: '准备章细纲',
  outlines: '准备整卷细纲',
  generating: '生成正文',
  prose: '生成正文',
  mech_check: '机械检查',
  review: '审校阶段',
  fix: '有限定向修复',
  draft_context: '校验批次临时上下文',
  awaiting_outline_confirmation: '等待人工确认',
  awaiting_final_review: '等待人工审核',
  finalize: '人工定稿写回',
  // 服务端 production._progress 实际上报的粗粒度阶段；缺映射时界面会显示
  // "服务端阶段：model"这种半英文，看起来像卡住（用户实际踩到过）。
  model: '正在调用模型',
  chapter: '逐章生成中',
  prepared: '候选已就绪，待确认',
}

/** 单次模型调用可能数分钟：阶段名不变不等于卡死，界面需要明确说出来。 */
export const SLOW_PHASES = new Set(['model', 'chapter', 'outline', 'outlines'])
export const jobLabel = (status: string): string =>
  JOB_LABELS[status] ?? `未知任务状态（${status || '未提供'}），仅可查看`
export const phaseLabel = (phase: string): string =>
  PHASE_LABELS[phase] ?? `服务端阶段：${phase || '未提供'}`
export const isRunning = (job: ProductionJob | null): boolean =>
  Boolean(job && ['preparing', 'generating'].includes(job.status))
export const canResume = (job: ProductionJob | null): boolean =>
  Boolean(job && ['paused', 'interrupted', 'error'].includes(job.status))
/** 只有已结束的任务能删：运行中的必须先停止后续生产（服务端同样拒绝）。 */
export const canRemove = (job: ProductionJob | null): boolean =>
  Boolean(job && !['preparing', 'generating'].includes(job.status))
export const canCancel = (job: ProductionJob | null): boolean =>
  Boolean(
    job &&
    [
      'preparing',
      'generating',
      'paused',
      'interrupted',
      'error',
      'awaiting_outline_confirmation',
      'awaiting_final_review',
      'partially_applied',
      'stale',
    ].includes(job.status),
  )
export const countValid = (count: unknown): count is number =>
  typeof count === 'number' && Number.isInteger(count) && count >= 1 && count <= 50
export const record = (value: unknown): value is Record<string, unknown> =>
  Boolean(value && typeof value === 'object' && !Array.isArray(value))
const numbers = (value: unknown): value is number[] =>
  Array.isArray(value) &&
  value.every((n) => Number.isInteger(n) && n > 0) &&
  new Set(value).size === value.length
const texts = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((s) => typeof s === 'string')
const text = (value: unknown): value is string => typeof value === 'string'
function invalid(message: string): never {
  throw new Error(`服务版本或任务数据不完整：${message}；保留已有输入，仅可刷新核对`)
}

export function assertJob(
  value: unknown,
  book: string,
  id?: string,
): asserts value is ProductionJob {
  if (
    !record(value) ||
    value.book !== book ||
    !text(value.id) ||
    !value.id ||
    (id && value.id !== id) ||
    !text(value.etag) ||
    !value.etag ||
    !text(value.status) ||
    !text(value.phase) ||
    !numbers(value.numbers) ||
    !numbers(value.completed) ||
    !numbers(value.applied) ||
    !record(value.request) ||
    !text(value.created_at) ||
    !text(value.updated_at) ||
    !(
      value.current_chapter === null ||
      (Number.isInteger(value.current_chapter) && Number(value.current_chapter) > 0)
    ) ||
    !(value.reason === null || text(value.reason))
  )
    invalid('任务范围、版本或所属书缺失')
  const ns = value.numbers
  if (value.completed.some((n) => !ns.includes(n)) || value.applied.some((n) => !ns.includes(n)))
    invalid('任务完成章不在本批范围')
}
export function assertVolumes(value: unknown, book: string): asserts value is VolumesResponse {
  if (
    !record(value) ||
    value.book !== book ||
    !text(value.title) ||
    !text(value.etag) ||
    !value.etag ||
    !Array.isArray(value.volumes) ||
    !Array.isArray(value.jobs) ||
    !texts(value.warnings)
  )
    invalid('卷列表/任务列表/警告或书版本缺失')
  for (const v of value.volumes) {
    if (
      !record(v) ||
      !Number.isInteger(v.no) ||
      Number(v.no) < 1 ||
      !text(v.name) ||
      !text(v.goal) ||
      !Array.isArray(v.chapter_range) ||
      ![0, 2].includes(v.chapter_range.length) ||
      !Array.isArray(v.chapters)
    )
      invalid('卷结构未知')
    if (
      v.chapter_range.length &&
      (!v.chapter_range.every((n) => Number.isInteger(n) && n > 0) ||
        v.chapter_range[0] > v.chapter_range[1])
    )
      invalid('卷章号范围无效')
    // 计数缺失不推断为零；正文与编辑入口仍可读取。
    for (const k of ['planned_count', 'draft_count', 'finalized_count'])
      if (!(typeof v[k] === 'number' && Number.isInteger(v[k]) && Number(v[k]) >= 0)) v[k] = null
    for (const c of v.chapters) {
      if (
        !record(c) ||
        !Number.isInteger(c.no) ||
        Number(c.no) < 1 ||
        !text(c.title) ||
        !text(c.status)
      )
        invalid('章节导航字段缺失')
      if (!(typeof c.word_count === 'number' && Number.isFinite(c.word_count) && c.word_count >= 0))
        c.word_count = null
    }
  }
  for (const j of value.jobs) assertJob(j, book)
}
export function parseBundle(source: string): ProductionBundle {
  const b: unknown = JSON.parse(source)
  if (
    !record(b) ||
    b.schema_version !== 1 ||
    !record(b.outline) ||
    !record(b.outline.fm) ||
    !Array.isArray(b.outline.rows) ||
    !('rhythm' in b.outline) ||
    !record(b.files) ||
    !Object.values(b.files).every(text) ||
    !Array.isArray(b.outlines) ||
    !texts(b.notes)
  )
    invalid('候选 schema_version 或结构未知')
  for (const o of b.outlines)
    if (
      !record(o) ||
      !Number.isInteger(o.no) ||
      Number(o.no) < 1 ||
      !record(o.fm) ||
      !text(o.body) ||
      o.fm.confirmed === true
    )
      invalid('细纲字段无效；候选不能自行标记为已确认')
  return b as unknown as ProductionBundle
}
export function assertReview(
  value: unknown,
  book: string,
  id: string,
): asserts value is ProductionReview {
  if (!record(value)) invalid('缺少审核读模型')
  assertJob(value.job, book, id)
  if (
    !text(value.preview_digest) ||
    !Array.isArray(value.files) ||
    !Array.isArray(value.chapters) ||
    !texts(value.warnings)
  )
    invalid('审核列表字段缺失')
  if (value.candidate !== null) parseBundle(JSON.stringify(value.candidate))
  for (const f of value.files)
    if (!record(f) || !text(f.path) || !text(f.before) || !text(f.after))
      invalid('文件影响结构未知')
  for (const c of value.chapters)
    if (
      !record(c) ||
      !Number.isInteger(c.no) ||
      !text(c.title) ||
      !text(c.body) ||
      !text(c.fingerprint) ||
      !text(c.outline_fingerprint) ||
      !text(c.status) ||
      !(c.check === null || record(c.check)) ||
      !(c.review === null || record(c.review))
    )
      invalid('稿件/指纹/检查结构缺失')
}
export function rangeText(ns: number[]): string {
  const sorted = [...new Set(ns)].sort((a, b) => a - b)
  const result: string[] = []
  for (let i = 0; i < sorted.length; i++) {
    const start = sorted[i]!
    let end = start
    while (sorted[i + 1] === end + 1) end = sorted[++i]!
    result.push(start === end ? `${start}` : `${start}—${end}`)
  }
  return result.join('、') || '无'
}
export function finalizable(c: ReviewChapter): boolean {
  return (
    c.status === 'pending_finalize' &&
    Boolean(c.fingerprint && c.outline_fingerprint && c.check && c.review) &&
    c.check?.stale !== true &&
    c.review?.stale !== true
  )
}
export function continuousSelection(
  chapters: ReviewChapter[],
  selected: number[],
  read: Record<string, string>,
): boolean {
  const ns = [...new Set(selected)].sort((a, b) => a - b)
  return (
    ns.length > 0 &&
    ns.length === selected.length &&
    ns.every((n, i) => {
      const c = chapters.find((row) => row.no === n)
      return (
        c &&
        finalizable(c) &&
        read[String(n)] === c.fingerprint &&
        (i === 0 || n === ns[i - 1]! + 1)
      )
    })
  )
}
export function checkText(value: Record<string, unknown> | null): string {
  if (!value) return '未提供检查结果，不能视为通过'
  if (value.stale === true) return '检查已过期，须回到章节工作台核对'
  const verdict = value.verdict
  const labels: Record<string, string> = {
    pass: '通过',
    ok: '通过',
    block: '阻断',
    warn: '有提醒',
    human_review: '需人工处理',
    pending_finalize: '待人工定稿',
  }
  return typeof verdict === 'string'
    ? (labels[verdict] ?? `未知结论：${verdict}（请展开原始结果）`)
    : '结果未提供可识别结论，请展开核对'
}
