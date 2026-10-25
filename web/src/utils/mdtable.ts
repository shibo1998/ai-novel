// Markdown 表格 ↔ 对象数组（伏笔台账 / 大纲总表共用，行为与旧版前端一致）。
export function parseMdTable(text: string): Record<string, string>[] {
  const lines = text.split(/\r?\n/).filter((l) => l.trim().startsWith("|"));
  if (lines.length < 2) throw new Error("表格格式错误");
  const cells = (l: string): string[] =>
    l.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  const cols = cells(lines[0]!);
  return lines
    .slice(1)
    .filter((l) => !/^[\s|:-]+$/.test(l))
    .map((l) => {
      const cs = cells(l);
      const row: Record<string, string> = {};
      cols.forEach((c, i) => {
        row[c] = cs[i] ?? "";
      });
      return row;
    });
}

export function toMdTable(cols: string[], rows: Record<string, unknown>[]): string {
  const head = `| ${cols.join(" | ")} |`;
  const sep = `| ${cols.map(() => "----").join(" | ")} |`;
  const body = rows.map((r) => `| ${cols.map((c) => String(r[c] ?? "")).join(" | ")} |`);
  return [head, sep, ...body].join("\n");
}
