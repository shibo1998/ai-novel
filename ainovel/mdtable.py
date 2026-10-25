"""Markdown 表格解析/序列化小工具（台账、大纲总表、地点表共用）。"""
from __future__ import annotations

import re
from typing import Any


def parse_table(text: str, header_marker: str) -> tuple[list[str], list[dict[str, str]]]:
    """找到以 header_marker 开头行的表格，返回 (列名, 行字典列表)。

    行字典缺列补空串，多余列丢弃。找不到表头返回 ([], [])。
    """
    lines = text.splitlines()
    header_idx = None
    cols: list[str] = []
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("|") and header_marker in s:
            header_idx = i
            cols = [c.strip() for c in s.strip("|").split("|")]
            break
    if header_idx is None:
        return [], []
    rows: list[dict[str, str]] = []
    for line in lines[header_idx + 2:]:  # 跳过分隔行
        s = line.strip()
        if not s.startswith("|"):
            break
        cells = [c.strip() for c in s.strip("|").split("|")]
        row = {cols[j]: (cells[j] if j < len(cells) else "") for j in range(len(cols))}
        rows.append(row)
    return cols, rows


def dump_table(cols: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(cols) + " |", "| " + " | ".join("-" * max(4, len(c)) for c in cols) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


_CELL_SPLIT_RE = re.compile(r"\s*\|\s*")


def split_pipes(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]
