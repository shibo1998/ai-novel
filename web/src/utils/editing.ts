// 规格07 §0.4：固定编辑目标、请求序号与脏数据离开确认。
export interface EditTarget {
  book: string
  resource: string
  chapter?: number
  baseline: string | number | null
}

export class SessionRequestGate {
  private generation = 0
  private latest = 0

  begin(target: EditTarget): { generation: number; request: number; target: EditTarget } {
    const ticket = { generation: this.generation, request: ++this.latest, target }
    return ticket
  }

  switchTarget(): number {
    this.generation++
    return this.generation
  }

  accepts(
    ticket: { generation: number; request: number; target: EditTarget },
    current: EditTarget,
  ): boolean {
    return (
      ticket.generation === this.generation &&
      ticket.request === this.latest &&
      sameTarget(ticket.target, current)
    )
  }
}

export function sameTarget(a: EditTarget, b: EditTarget): boolean {
  return a.book === b.book && a.resource === b.resource && a.chapter === b.chapter
}

export type DirtyDecision = 'save' | 'discard' | 'cancel'
export type DirtyGuard = () => Promise<DirtyDecision>

let activeGuard: DirtyGuard | null = null
let activeDirty: () => boolean = () => false
let beforeUnloadInstalled = false

export function registerDirtyGuard(guard: DirtyGuard, dirty: () => boolean): () => void {
  activeGuard = guard
  activeDirty = dirty
  if (!beforeUnloadInstalled && typeof window !== 'undefined') {
    window.addEventListener('beforeunload', (event) => {
      if (!activeDirty()) return
      event.preventDefault()
      event.returnValue = ''
    })
    // 本应用只有一个可编辑主页面；页面卸载时由 unregister 清理 guard。
    beforeUnloadInstalled = true
  }
  return () => {
    if (activeGuard === guard) activeGuard = null
  }
}

export async function confirmDirtyLeave(): Promise<boolean> {
  if (!activeGuard) return true
  return (await activeGuard()) !== 'cancel'
}
