// 规格07 UI-23：仅管理本次会话等待，不冒充服务端任务系统。
import { computed, reactive } from 'vue'
import { recordError } from './errors'

export type TaskPhase = 'running' | 'unknown' | 'completed' | 'failed'
export interface TaskRecord {
  id: number
  operation: string
  book?: string
  chapter?: number
  phase: TaskPhase
  startedAt: number
  endedAt?: number
  elapsed: number
  result: string
  write: boolean
  returnPath?: string
  confirm?: () => Promise<string>
  controller?: AbortController
}

export interface TaskOptions {
  operation: string
  book?: string
  chapter?: number
  write?: boolean
  returnPath?: string
  confirm?: () => Promise<string>
}

export const tasks = reactive<TaskRecord[]>([])
export const nowTick = reactive({ value: Date.now() })
let nextTaskId = 1
let ticker: ReturnType<typeof setInterval> | null = null

function ensureTicker(): void {
  if (ticker) return
  ticker = setInterval(() => {
    nowTick.value = Date.now()
    for (const task of tasks) {
      if (task.phase === 'running')
        task.elapsed = Math.floor((nowTick.value - task.startedAt) / 1000)
    }
  }, 1000)
}

export function taskElapsed(task: TaskRecord): number {
  return task.phase === 'running'
    ? Math.floor((nowTick.value - task.startedAt) / 1000)
    : task.elapsed
}

export const pendingWriteBooks = computed(
  () =>
    new Set(
      tasks
        .filter((t) => t.write && (t.phase === 'running' || t.phase === 'unknown'))
        .map((t) => t.book ?? ''),
    ),
)

export function hasPendingWrite(book: string): boolean {
  return tasks.some(
    (t) => t.book === book && t.write && (t.phase === 'running' || t.phase === 'unknown'),
  )
}

/** 先登记任务再执行；executor 必须实际使用 signal，不能传入已发出的 Promise。 */
export async function withTask<T>(
  options: TaskOptions,
  executor: (signal: AbortSignal, task: TaskRecord) => Promise<T>,
): Promise<T> {
  const controller = new AbortController()
  const task = reactive<TaskRecord>({
    id: nextTaskId++,
    operation: options.operation,
    book: options.book,
    chapter: options.chapter,
    phase: 'running',
    startedAt: Date.now(),
    elapsed: 0,
    result: '等待响应',
    write: options.write ?? false,
    returnPath: options.returnPath,
    confirm: options.confirm,
    controller,
  })
  tasks.unshift(task)
  if (tasks.length > 30) tasks.splice(30)
  ensureTicker()
  try {
    const result = await executor(controller.signal, task)
    // 主动停止后晚到的响应只能完成原任务，不能更新其他目标；页面仍需自行校验会话。
    task.phase = 'completed'
    task.result = '已收到明确成功回执'
    task.endedAt = Date.now()
    task.elapsed = Math.floor((task.endedAt - task.startedAt) / 1000)
    return result
  } catch (error) {
    task.endedAt = Date.now()
    task.elapsed = Math.floor((task.endedAt - task.startedAt) / 1000)
    if (controller.signal.aborted) {
      task.phase = 'unknown'
      task.result = task.write
        ? '已停止等待；后台可能继续写入并产生费用，请确认原目标结果'
        : '已停止等待；纯返回体结果可能无法取回'
    } else {
      const code = (error as { code?: string })?.code
      const indeterminate =
        options.write && (!code || ['ERR_NETWORK', 'ECONNABORTED', 'SSE_INCOMPLETE'].includes(code))
      task.phase = indeterminate ? 'unknown' : 'failed'
      task.result = indeterminate
        ? '请求断开，后台可能已执行；结果待确认，不自动重试'
        : error instanceof Error
          ? error.message
          : String(error)
      // Axios 已由拦截器记录；fetch/SSE 在此统一补记，避免同一失败重复通知。
      if (!(error as { response?: unknown })?.response) {
        recordError(error, {
          operation: options.operation,
          book: options.book,
          chapter: options.chapter,
          write: options.write,
        })
      }
    }
    throw error
  } finally {
    task.controller = undefined
  }
}

export function stopWaiting(id: number): void {
  const task = tasks.find((t) => t.id === id)
  if (!task || task.phase !== 'running') return
  task.phase = 'unknown'
  task.result = task.write
    ? '已停止等待；后台可能继续执行并写入文件、产生费用'
    : '已停止等待；候选未接收时无法确认生成结果'
  task.controller?.abort()
  task.controller = undefined
}

export async function confirmTask(id: number): Promise<string> {
  const task = tasks.find((t) => t.id === id)
  if (!task) return '任务不存在'
  if (!task.confirm) {
    task.result = task.write
      ? '请回到原目标刷新并核对文件；仍无法确定时不会自动重试'
      : '候选未接收，无法确认生成结果；确认可能重复调用和费用后可手动重试'
    return task.result
  }
  try {
    const result = await task.confirm()
    task.phase = 'completed'
    task.result = result
    return result
  } catch (error) {
    task.phase = 'unknown'
    task.result = `仍无法确认：${error instanceof Error ? error.message : String(error)}`
    return task.result
  }
}

export function resetTasksForTest(): void {
  tasks.splice(0)
  if (ticker) clearInterval(ticker)
  ticker = null
}
