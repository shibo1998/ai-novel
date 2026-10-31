// 规格07 UI-28/29：结构化手改值、可执行动作与可靠顺序批量。
import type { Conflict } from '@/api/types'

export type ConflictAction = 'adopt_ai' | 'keep_archive' | 'manual'
export type BatchItemStatus = 'pending' | 'success' | 'failed' | 'unknown' | 'skipped'
export interface BatchItemResult {
  id: number
  status: BatchItemStatus
  message: string
  effect?: string
}

export function allowedActions(item: Conflict): ConflictAction[] {
  if (Array.isArray(item.allowed_actions)) {
    return item.allowed_actions.filter((a): a is ConflictAction =>
      ['adopt_ai', 'keep_archive', 'manual'].includes(String(a)),
    )
  }
  const informational = ['realm_name', 'foreshadow_op'].includes(item.kind) || !knownKind(item.kind)
  if (informational) return ['keep_archive']
  const actions: ConflictAction[] = ['keep_archive', 'manual']
  if (candidateComplete(item)) actions.unshift('adopt_ai')
  return actions
}

export function knownKind(kind: string): boolean {
  return [
    'new_setting',
    'timeline_candidate',
    'foreshadow_op',
    'dead_reappear',
    'missing_char',
    'realm_regress',
    'realm_name',
    'unknown_field',
  ].includes(kind)
}

export function candidateComplete(item: Conflict): boolean {
  if (item.kind === 'unknown_field' || item.kind === 'realm_name' || item.kind === 'foreshadow_op')
    return false
  if (item.new === null || item.new === undefined || item.new === '') return false
  if (item.kind === 'new_setting') {
    const v = asObject(item.new)
    return Boolean(v && nonempty(v.term) && nonempty(v.description))
  }
  if (item.kind === 'timeline_candidate') {
    const v = asObject(item.new)
    return Boolean(
      v && Number.isInteger(Number(v.chapter_no)) && Number(v.chapter_no) > 0 && nonempty(v.event),
    )
  }
  if (item.kind === 'missing_char') {
    const v = asObject(item.new)
    return Boolean(v && nonempty(v.name) && typeof v.status === 'object' && v.status !== null)
  }
  return true
}

export function initialManualValue(item: Conflict): unknown {
  if (item.kind === 'new_setting') return { term: '', description: '' }
  if (item.kind === 'timeline_candidate') {
    const v = asObject(item.new) ?? {}
    return {
      chapter_no: Number(v.chapter_no ?? item.chapter_no) || item.chapter_no,
      event: String(v.event ?? ''),
      characters: Array.isArray(v.characters) ? v.characters.map(String) : [],
      reader_known: typeof v.reader_known === 'boolean' ? v.reader_known : false,
    }
  }
  if (item.kind === 'missing_char') {
    const v = asObject(item.new) ?? {}
    return {
      name: String(v.name ?? item.char ?? ''),
      role: String(v.role ?? '龙套'),
      status: typeof v.status === 'object' && v.status !== null ? v.status : {},
    }
  }
  if (item.kind === 'unknown_field') return null
  if (['injuries', 'inventory', 'known_info'].includes(item.field ?? '')) {
    return Array.isArray(item.new) ? [...item.new] : []
  }
  if (item.field === 'alive') return typeof item.new === 'boolean' ? item.new : true
  return typeof item.new === 'string' ? item.new : ''
}

export function validateManualValue(item: Conflict, value: unknown): string | null {
  const v = asObject(value)
  if (item.kind === 'new_setting') {
    if (!v || !nonempty(v.term) || !nonempty(v.description)) return '术语与说明均必填'
  } else if (item.kind === 'timeline_candidate') {
    if (!v || !Number.isInteger(v.chapter_no) || Number(v.chapter_no) <= 0 || !nonempty(v.event))
      return '章号须为正整数，事件必填'
    if (!Array.isArray(v.characters) || typeof v.reader_known !== 'boolean')
      return '角色须为列表，读者知情须为布尔值'
  } else if (item.kind === 'missing_char') {
    if (!v || !nonempty(v.name)) return '角色名必填'
    if (!['主角', '女主', '反派', '重要配角', '龙套'].includes(String(v.role)))
      return '角色定位无效'
    if (!v.status || typeof v.status !== 'object' || Array.isArray(v.status))
      return '初始状态须为对象'
  } else if (item.kind === 'unknown_field') {
    return '未知字段不提供任意写回'
  } else if (item.field === 'alive' && typeof value !== 'boolean') {
    return '存亡必须保持布尔类型'
  } else if (
    ['injuries', 'inventory', 'known_info'].includes(item.field ?? '') &&
    !Array.isArray(value)
  ) {
    return '该字段必须保持字符串列表类型'
  }
  return null
}

/** 顺序执行；明确失败继续，网络/占用/结果未知立即停止。已成功项绝不自动重试。 */
export async function runConflictBatch(
  items: Conflict[],
  action: ConflictAction,
  execute: (item: Conflict, action: ConflictAction) => Promise<{ effect?: string }>,
  shouldStop: () => boolean = () => false,
): Promise<BatchItemResult[]> {
  const results: BatchItemResult[] = []
  let halted = false
  for (const item of items) {
    if (halted || shouldStop()) {
      results.push({ id: item.id, status: 'skipped', message: '未执行' })
      continue
    }
    try {
      const actualAction = action === 'manual' ? 'manual' : action
      const response = await execute(item, actualAction)
      results.push({
        id: item.id,
        status: 'success',
        message: '已收到明确成功回执',
        effect: response.effect,
      })
    } catch (error) {
      const e = error as {
        response?: { status?: number; data?: { detail?: { code?: string } } }
        code?: string
        message?: string
      }
      const code = e.response?.data?.detail?.code ?? e.code ?? ''
      const status = e.response?.status
      const unknown =
        !status || code === 'BOOK_BUSY' || code === 'ERR_NETWORK' || code === 'ECONNABORTED'
      results.push({
        id: item.id,
        status: unknown ? 'unknown' : 'failed',
        message: e.message ?? '裁决失败',
      })
      if (unknown) halted = true
    }
  }
  return results
}

function asObject(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}
function nonempty(value: unknown): boolean {
  return typeof value === 'string' && Boolean(value.trim())
}
