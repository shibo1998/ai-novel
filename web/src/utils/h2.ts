// Markdown H2 节解析/序列化（角色卡 10 个固定 H2 节、Bible 7 个固定节共用）。
// 与后端 ainovel/bible.py `_parse_sections` 行为对齐：按行首 `## ` 切节，标题行算节名。

export interface H2Section {
  title: string
  content: string
}

export interface ParsedH2 {
  /** 第一个 `## ` 之前的内容（通常为空） */
  preamble: string
  sections: H2Section[]
}

/** 按 `## 标题` 切节（保留原始顺序；同名节只取第一个，避免序列化后互相覆盖）。 */
export function parseH2Sections(body: string): ParsedH2 {
  const parts = body.replace(/\r\n/g, '\n').split(/^##\s+/m)
  const preamble = (parts[0] ?? '').trim()
  const seen = new Set<string>()
  const sections: H2Section[] = []
  for (const part of parts.slice(1)) {
    const lines = part.split('\n')
    const title = (lines[0] ?? '').trim()
    if (!title || seen.has(title)) continue
    seen.add(title)
    sections.push({ title, content: lines.slice(1).join('\n').trim() })
  }
  return { preamble, sections }
}

/** 序列化回 Markdown（稳定格式：`## 标题\n\n内容\n`，节间空行）。 */
export function dumpH2Sections(sections: H2Section[], preamble = ''): string {
  const head = preamble.trim() ? `${preamble.trim()}\n\n` : ''
  return head + sections.map((s) => `## ${s.title}\n\n${s.content.trim()}\n`).join('\n')
}

/** 取某节内容（不存在返回空串），用于把 Bible 各节文本喂给表单/摘要。 */
export function sectionContent(sections: H2Section[], title: string): string {
  return sections.find((s) => s.title === title)?.content ?? ''
}
