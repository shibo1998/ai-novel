// 规格07 UI-24：Axios、fetch、SSE 与本地校验共用会话错误记录。
import { reactive } from 'vue'
import { ElMessage } from 'element-plus'

export interface ErrorContext {
  operation?: string
  book?: string
  chapter?: number
  background?: boolean
  write?: boolean
}

export interface ErrorRecord {
  id: number
  time: string
  operation: string
  book?: string
  chapter?: number
  code: string
  message: string
  count: number
  action: string
  write?: boolean
}

export interface ParsedError {
  code: string
  message: string
  status?: number
  action: string
}

export const errorRecords = reactive<ErrorRecord[]>([])
let nextId = 1
const openMessages = new Map<string, { close: () => void }>()

export function parseError(error: unknown): ParsedError {
  const e = error as {
    response?: { status?: number; data?: { detail?: unknown } }
    message?: string
    code?: string
    name?: string
  }
  const status = e?.response?.status
  const detail = e?.response?.data?.detail
  let code = typeof e?.code === 'string' && e.code !== 'ERR_BAD_REQUEST' ? e.code : 'REQUEST_FAILED'
  let message = e?.message || '请求失败'
  if (typeof detail === 'string') message = detail
  if (Array.isArray(detail)) {
    code = 'VALIDATION_ERROR'
    message = detail
      .map((item) => {
        const v = item as { loc?: unknown[]; msg?: string }
        return `${(v.loc ?? []).slice(1).join('.') || '参数'}：${v.msg ?? '格式错误'}`
      })
      .join('；')
  }
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const d = detail as { code?: unknown; message?: unknown }
    if (typeof d.code === 'string') code = d.code
    if (typeof d.message === 'string') message = d.message
  }
  if (status === 422 && code === 'REQUEST_FAILED') code = 'VALIDATION_ERROR'
  if (status === 404 && code === 'REQUEST_FAILED') code = 'NOT_FOUND'
  if (code === 'ECONNABORTED' || code === 'ERR_NETWORK')
    message = '网络连接或请求超时，请检查服务状态'
  return { code, message, status, action: recoveryAction(code, status) }
}

export function recoveryAction(code: string, status?: number): string {
  if (code === 'OUTLINE_ROW_MISSING' || code === 'OUTLINE_INVALID')
    return '去补大纲或修复档案，再返回原章重试'
  if (code === 'BOOK_BUSY') return '等待本书已有任务完成，并在任务条确认结果'
  if (code === 'RESOURCE_CHANGED' || status === 412) return '保留修改，复制内容并查看当前文件后合并'
  if (code === 'CONFLICT_CHANGED') return '刷新冲突与目标档案，重新判断后再裁决'
  if (code === 'CONFLICT_VALUE_INVALID') return '按字段类型修正输入，原裁决仍待处理'
  if (code === 'VALIDATION_ERROR' || status === 422) return '按具体字段原因修正输入后重试'
  if (code === 'NOT_FOUND' || status === 404) return '返回书架或书总览，检查资源是否仍存在'
  if (code === 'CONFLICTS_PENDING') return '先处理裁决队列，再重新执行'
  if (code === 'STATE_NOT_ALLOWED') return '刷新章节状态，按中文提示恢复到可执行阶段'
  return '检查服务后可重试；写操作需先确认实际结果，避免重复写入'
}

export function recordError(error: unknown, context: ErrorContext = {}): ErrorRecord {
  const parsed = parseError(error)
  const operation = context.operation ?? '请求'
  const key = `${operation}|${context.book ?? ''}|${context.chapter ?? ''}|${parsed.code}|${parsed.message}`
  const existing = errorRecords.find(
    (r) => `${r.operation}|${r.book ?? ''}|${r.chapter ?? ''}|${r.code}|${r.message}` === key,
  )
  if (existing) {
    existing.count++
    existing.time = new Date().toISOString()
    if (!context.background) notify(existing, key)
    return existing
  }
  const record: ErrorRecord = {
    id: nextId++,
    time: new Date().toISOString(),
    operation,
    book: context.book,
    chapter: context.chapter,
    code: parsed.code,
    message: parsed.message,
    count: 1,
    action: parsed.action,
    write: context.write,
  }
  errorRecords.unshift(record)
  if (errorRecords.length > 20) errorRecords.splice(20)
  if (!context.background) notify(record, key)
  return record
}

function notify(record: ErrorRecord, key: string): void {
  openMessages.get(key)?.close()
  const handler = ElMessage.error({
    message: `${record.operation}：${record.message}。下一步：${record.action}`,
    duration: 0,
    showClose: true,
    onClose: () => openMessages.delete(key),
  })
  openMessages.set(key, handler)
}

export function reportValidation(message: string, context: ErrorContext = {}): ErrorRecord {
  return recordError({ code: 'VALIDATION_ERROR', message }, context)
}

export function copyError(record: ErrorRecord): string {
  return [
    `时间：${record.time}`,
    `操作：${record.operation}`,
    record.book ? `书：${record.book}` : '',
    record.chapter ? `章：${record.chapter}` : '',
    `错误码：${record.code}`,
    `原因：${record.message}`,
    `出现次数：${record.count}`,
    `下一步：${record.action}`,
  ]
    .filter(Boolean)
    .join('\n')
}
