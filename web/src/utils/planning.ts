// 内容规划纯函数层（规格08 §3.3 阶段B）：只做展示格式化与默认批次计算，
// 就绪状态一律展示服务端算出的结果，前端不自算哈希、不猜状态。
import type {
  PlanningLayer,
  PlanningModel,
  PlanningPreview,
  PlanningScope,
  PlanningStatus,
  PlanningTarget,
} from '@/api/types'

export const PLANNING_SCOPE_LABELS: Record<PlanningScope, string> = {
  global: '全书粗纲',
  volume: '卷纲',
  unit: '单元详情',
  chapters: '章纲批次',
}

export const PLANNING_STATUS_TAG: Record<
  PlanningStatus,
  'primary' | 'success' | 'warning' | 'info' | 'danger'
> = {
  missing: 'info',
  unadopted: 'warning',
  ready: 'success',
  stale: 'warning',
  invalid: 'danger',
}

export const PLANNING_STATUS_LABEL: Record<PlanningStatus, string> = {
  missing: '未建立',
  unadopted: '待采纳',
  ready: '已采纳',
  stale: '待复核',
  invalid: '无法判定',
}

/** 层键（与服务端 planning.adoptions 的键格式一致，规格08 §2.3） */
export function planningNodeKey(scope: PlanningScope, target: PlanningTarget): string {
  if (scope === 'global') return 'global'
  if (scope === 'volume') return `volume:${target.volume_no}`
  if (scope === 'unit') return `unit:${target.unit_id}`
  const [lo, hi] = target.chapters ?? [0, 0]
  return `chapters:${target.unit_id}:${lo}-${hi}`
}

/** 单元内下一批建议（规格08 §3.3：默认取接下来最多 10 章，不跨单元） */
export function planningNextBatch(
  unitRange: number[] | null | undefined,
  coveredNumbers: readonly number[],
): [number, number] | null {
  if (!Array.isArray(unitRange) || unitRange.length !== 2) return null
  const [start, end] = unitRange
  if (!Number.isInteger(start) || !Number.isInteger(end) || start > end) return null
  const covered = new Set(coveredNumbers)
  for (let no = start; no <= end; no += 1) {
    if (!covered.has(no)) return [no, Math.min(no + 9, end)]
  }
  return null
}

/** 影响摘要（中文，给确认弹窗与预览面板用） */
export function planningImpactText(impact: PlanningPreview['impact']): string {
  const scope = impact.scope
  if (scope === 'global') {
    const added = impact.volumes_added ?? []
    const touched = impact.volumes_touched ?? []
    return `更新全书字段与 ${touched.length} 个卷的粗方向${added.length ? `；新增卷 ${added.join('、')}` : ''}；已展开卷细节/单元/章纲保留，正文不重写`
  }
  if (scope === 'volume') {
    const range = impact.volume_range ?? [0, 0]
    return `卷 ${impact.volume_no} 展开为章 ${range[0]}-${range[1]}（${range[1] - range[0] + 1} 章），生成 ${(impact.units_added ?? []).length} 个单元骨架；旧骨架被替换`
  }
  if (scope === 'unit')
    return `只细化单元 ${impact.unit_id} 的目标/阻力/选择/结果与信息边界；不改单元名称/范围，不改其他单元`
  const added = impact.added_numbers ?? []
  const changed = impact.changed_numbers ?? []
  const batch = impact.batch ?? impact.target?.chapters ?? [0, 0]
  return `章纲批次 ${batch[0]}-${batch[1]}：新增 ${added.length} 行、改动 ${changed.length} 行；批外章行与其他批次小节保留`
}

/** 预览 diff 文本（规范化 JSON；由 DiffViewer 呈现行级差异） */
export function planningProjectionText(projection: Record<string, unknown>): string {
  return JSON.stringify(projection, null, 2)
}

/** 就绪层的简短状态行 */
export function planningLayerLine(layer: PlanningLayer | undefined): string {
  if (!layer) return '未建立'
  return `${PLANNING_STATUS_LABEL[layer.status] ?? layer.status} · ${layer.message}`
}

/** 下一步文案（服务端 next_actions 的展示包装） */
export function planningNextActionText(model: PlanningModel): string {
  const first = model.next_actions[0]
  return first ? first.message : '规划就绪'
}

/** 工作台 G6 门禁（planning_gate）展示文本 */
export function planningGateText(
  gate: { allowed: boolean; code: string | null; message: string } | undefined,
): string {
  if (!gate) return ''
  return gate.allowed ? '' : gate.message || '规划链未就绪'
}
