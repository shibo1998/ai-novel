// 逐行 diff（规格02 §4「生成 → UI diff 预览 → 人工改定 → 采纳」的 diff 预览）。
// 手写 LCS 动态规划，不引第三方库：档案文档通常在数百行量级，
// O(n*m) 单元格完全够用；超过 MAX_CELLS 退化为按行集合比较（避免卡死浏览器）。

export type DiffKind = 'same' | 'add' | 'del'

export interface DiffLine {
  kind: DiffKind
  text: string
  /** 原文行号（1-based）；add 行为 null */
  oldLine: number | null
  /** 新文行号（1-based）；del 行为 null */
  newLine: number | null
}

/** LCS 单元格上限：n*m 超过此值则降级（约 2000 行 × 1000 行的规模）。 */
const MAX_CELLS = 2_000_000

function splitLines(text: string): string[] {
  // 末尾换行不算一行空内容，否则每次都会多一条虚假的相同行
  return text.replace(/\r\n/g, '\n').replace(/\n$/, '').split('\n')
}

/** 逐行 diff：返回按阅读顺序排列的 DiffLine 列表（含新旧两侧行号）。 */
export function diffLines(oldText: string, newText: string): DiffLine[] {
  const a = splitLines(oldText)
  const b = splitLines(newText)
  if (a.length * b.length > MAX_CELLS) return diffBySet(a, b)

  // lcs[i][j] = a[i:] 与 b[j:] 的最长公共子序列长度
  const n = a.length
  const m = b.length
  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i]![j] =
        a[i] === b[j] ? lcs[i + 1]![j + 1]! + 1 : Math.max(lcs[i + 1]![j]!, lcs[i]![j + 1]!)
    }
  }

  const out: DiffLine[] = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ kind: 'same', text: a[i]!, oldLine: i + 1, newLine: j + 1 })
      i++
      j++
    } else if (lcs[i + 1]![j]! >= lcs[i]![j + 1]!) {
      out.push({ kind: 'del', text: a[i]!, oldLine: i + 1, newLine: null })
      i++
    } else {
      out.push({ kind: 'add', text: b[j]!, oldLine: null, newLine: j + 1 })
      j++
    }
  }
  while (i < n) out.push({ kind: 'del', text: a[i]!, oldLine: ++i, newLine: null })
  while (j < m) out.push({ kind: 'add', text: b[j]!, oldLine: null, newLine: ++j })
  return out
}

/** 降级 diff：超大文档时用带前瞻的贪心匹配，不保证最短编辑序列但行号仍准确。 */
function diffBySet(a: string[], b: string[]): DiffLine[] {
  const out: DiffLine[] = []
  const lookahead = 200
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ kind: 'same', text: a[i]!, oldLine: i + 1, newLine: j + 1 })
      i++
      j++
      continue
    }
    // 在有限窗口内找 a[i] 在 b 中的落点 / b[j] 在 a 中的落点，二者取更近的一个
    let skipB = -1
    for (let k = j + 1; k < Math.min(b.length, j + lookahead); k++) {
      if (b[k] === a[i]) {
        skipB = k - j
        break
      }
    }
    let skipA = -1
    for (let k = i + 1; k < Math.min(a.length, i + lookahead); k++) {
      if (a[k] === b[j]) {
        skipA = k - i
        break
      }
    }
    if (skipB >= 0 && (skipA < 0 || skipB <= skipA)) {
      for (let k = 0; k < skipB; k++) {
        out.push({ kind: 'add', text: b[j + k]!, oldLine: null, newLine: j + k + 1 })
      }
      j += skipB
    } else if (skipA >= 0) {
      for (let k = 0; k < skipA; k++) {
        out.push({ kind: 'del', text: a[i + k]!, oldLine: i + k + 1, newLine: null })
      }
      i += skipA
    } else {
      out.push({ kind: 'del', text: a[i]!, oldLine: i + 1, newLine: null })
      out.push({ kind: 'add', text: b[j]!, oldLine: null, newLine: j + 1 })
      i++
      j++
    }
  }
  while (i < a.length) out.push({ kind: 'del', text: a[i]!, oldLine: ++i, newLine: null })
  while (j < b.length) out.push({ kind: 'add', text: b[j]!, oldLine: null, newLine: ++j })
  return out
}

export interface DiffStat {
  added: number
  removed: number
  unchanged: number
}

/** 变更统计（弹窗头部摘要用）。 */
export function diffStat(lines: DiffLine[]): DiffStat {
  let added = 0
  let removed = 0
  let unchanged = 0
  for (const l of lines) {
    if (l.kind === 'add') added++
    else if (l.kind === 'del') removed++
    else unchanged++
  }
  return { added, removed, unchanged }
}
