// 规格07 UI-14：正文显示分段与原始行号映射；任何函数都不修改输入正文。
export interface ProseParagraph {
  id: number
  text: string
  startLine: number
  endLine: number
}

export interface ProseMapping {
  paragraphs: ProseParagraph[]
  /** 原始 1 起始行号 → 段落下标；空行也映射到可阅读段落。 */
  lineToParagraph: Array<number | null>
}

/** 一个或多个空白行分段；段内单换行保留，CRLF 只在显示层规范化。 */
export function buildProseMapping(source: string): ProseMapping {
  const lines = source.replace(/\r\n?/g, '\n').split('\n')
  const raw: ProseParagraph[] = []
  let buffer: string[] = []
  let start = 0
  const flush = (endLine: number): void => {
    if (!buffer.length) return
    raw.push({ id: raw.length, text: buffer.join('\n'), startLine: start, endLine })
    buffer = []
  }
  lines.forEach((line, index) => {
    const lineNo = index + 1
    if (line.trim()) {
      if (!buffer.length) start = lineNo
      buffer.push(line)
    } else {
      flush(lineNo - 1)
    }
  })
  flush(lines.length)
  const lineToParagraph: Array<number | null> = Array.from({ length: lines.length + 1 }, () => null)
  raw.forEach((paragraph, index) => {
    for (let line = paragraph.startLine; line <= paragraph.endLine; line++) {
      lineToParagraph[line] = index
    }
  })
  // 空行优先映射到最近的后续非空段，无后续则映射到前段。
  for (let line = 1; line <= lines.length; line++) {
    if (lineToParagraph[line] !== null) continue
    const next = raw.findIndex((p) => p.startLine > line)
    lineToParagraph[line] = next >= 0 ? next : raw.length - 1
    if (lineToParagraph[line]! < 0) lineToParagraph[line] = null
  }
  return { paragraphs: raw, lineToParagraph }
}

export function paragraphForLine(mapping: ProseMapping, line: number): ProseParagraph | null {
  if (!Number.isInteger(line) || line < 1) return null
  const index = mapping.lineToParagraph[line]
  return index === null || index === undefined ? null : (mapping.paragraphs[index] ?? null)
}

/** 引用必须在原文中唯一出现；重复或无命中均不能猜测跳转位置。 */
export function paragraphForUniqueQuote(
  mapping: ProseMapping,
  source: string,
  quote: string,
): ProseParagraph | null {
  const needle = quote.trim()
  if (!needle) return null
  const normalized = source.replace(/\r\n?/g, '\n')
  const first = normalized.indexOf(needle)
  if (first < 0 || normalized.indexOf(needle, first + needle.length) >= 0) return null
  const line = normalized.slice(0, first).split('\n').length
  return paragraphForLine(mapping, line)
}
