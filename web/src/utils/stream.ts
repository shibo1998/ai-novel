// 规格07 UI-15：流式文本单缓冲与节流提交。由调用方提供定时器，便于离线确定性测试。
export class StreamTextBuffer {
  private pending = ''
  private rendered = ''
  private timer: ReturnType<typeof setTimeout> | null = null

  constructor(
    private readonly commit: (text: string) => void,
    private readonly interval = 100,
  ) {}

  append(text: string): void {
    this.pending += text
    if (this.timer !== null) return
    this.timer = setTimeout(() => {
      this.timer = null
      this.flush()
    }, this.interval)
  }

  flush(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer)
      this.timer = null
    }
    if (!this.pending) return
    this.rendered += this.pending
    this.pending = ''
    this.commit(this.rendered)
  }

  reset(): void {
    this.pending = ''
    this.rendered = ''
    if (this.timer !== null) clearTimeout(this.timer)
    this.timer = null
    this.commit('')
  }

  dispose(): void {
    this.flush()
  }

  get text(): string {
    return this.rendered + this.pending
  }
}

export interface FollowState {
  following: boolean
  showReturn: boolean
}

/** 距底部超过 48px 暂停跟随；自行回到底部恢复。 */
export function updateFollowState(
  scrollTop: number,
  clientHeight: number,
  scrollHeight: number,
): FollowState {
  const following = scrollHeight - clientHeight - scrollTop <= 48
  return { following, showReturn: !following }
}
