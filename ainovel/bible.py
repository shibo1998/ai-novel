"""STORY_BIBLE.md：固定 H2 节读写（规格01 §2.2）。程序按节解析。"""
from __future__ import annotations

import re
from typing import Any

from . import atomic, books
from .mdtable import dump_table

POWER_COLS = ["大境界", "子境界", "突破条件", "能力边界(不能做什么)", "战力对标", "寿元", "代价与风险", "突破异象"]
HIDDEN_COLS = ["规则内容", "隐藏等级", "计划揭晓章节"]
TERM_COLS = ["术语", "含义"]


def _parse_sections(text: str) -> dict[str, str]:
    """返回 {H2标题: 该节内容}。"""
    parts = re.split(r"(?m)^##\s+", text)
    sections: dict[str, str] = {}
    for part in parts[1:]:
        lines = part.splitlines()
        title = lines[0].strip()
        sections[title] = "\n".join(lines[1:]).strip()
    return sections


def read(book: str) -> dict[str, str]:
    path = books.book_dir(book) / "STORY_BIBLE.md"
    return _parse_sections(path.read_text(encoding="utf-8"))


def write(book: str, sections: dict[str, str]) -> None:
    """整本写回：保留固定节顺序，未知节追加尾部。"""
    ordered = [s for s in books.BIBLE_SECTIONS if s in sections] + [
        s for s in sections if s not in books.BIBLE_SECTIONS
    ]
    body = "\n".join(f"## {s}\n\n{sections[s].strip()}\n" for s in ordered if sections[s] is not None)
    atomic.write_text(books.book_dir(book) / "STORY_BIBLE.md", f"# 世界观圣经\n\n{body}")


def update_section(book: str, title: str, content: str) -> None:
    sections = read(book)
    sections[title] = content
    write(book, sections)


def power_rows(book: str) -> list[dict[str, str]]:
    """力量体系固定表格 → 行列表（装配器/境界回退校验用）。"""
    from .mdtable import parse_table

    content = read(book).get("力量体系", "")
    _, rows = parse_table(content, "大境界")
    return rows


def realm_order(book: str) -> list[str]:
    """境界先后序列（大境界去重 + 子境界展平），供"境界倒退"冲突检测。"""
    order: list[str] = []
    for row in power_rows(book):
        for cell in (row.get("大境界", ""), row.get("子境界", "")):
            for r in [x for x in re.split(r"[·/、,，\s]+", cell) if x]:
                if r not in order:
                    order.append(r)
    return order


def terms(book: str) -> dict[str, str]:
    from .mdtable import parse_table

    _, rows = parse_table(read(book).get("术语表", ""), "术语")
    return {r.get("术语", ""): r.get("含义", "") for r in rows if r.get("术语")}
