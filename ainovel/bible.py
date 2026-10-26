"""STORY_BIBLE.md：固定 H2 节读写（规格01 §2.2）。程序按节解析。"""
from __future__ import annotations

import logging
import re
from typing import Any

from . import atomic, books
from .mdtable import dump_table, parse_table

logger = logging.getLogger(__name__)

POWER_COLS = ["大境界", "子境界", "突破条件", "能力边界(不能做什么)", "战力对标", "寿元", "代价与风险", "突破异象"]
HIDDEN_COLS = ["规则内容", "隐藏等级", "计划揭晓章节"]
TERM_COLS = ["术语", "含义"]

# 节名 → 该节固定表格的必需列（规格01 §2.2）。用于写回时校验表头，
# 防止手改或 AI 采纳时列名漂移导致下游解析静默失效。
TABLE_COLS: dict[str, list[str]] = {
    "力量体系": POWER_COLS,
    "隐藏规则": HIDDEN_COLS,
    "术语表": TERM_COLS,
}


class BibleError(Exception):
    """Bible 结构非法（规格01 §5.2：校验失败拒绝写回，不静默降级）。"""


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


def validate_sections(sections: dict[str, str]) -> list[str]:
    """校验固定表格节的表头（规格01 §2.2）。

    返回问题列表（空 = 通过）。这是"防护悄悄失效"类缺陷的解药：
    力量体系列名写错 → power_rows() 返回 [] → 境界倒退检测静默失效，
    原来没人报错，用户以为防护在工作。

    区分两种"没有表头"：
    - 该节还没有表格（如「（待生成）」占位或纯文字说明）→ 不校验，等它成表再说；
    - 该节已有表格但列名不对 → 必须报错，这是会静默拖垮下游解析的情况。
    """
    problems: list[str] = []
    for sec, need in TABLE_COLS.items():
        content = sections.get(sec)
        if not content or not content.strip():
            continue  # 空节不校验
        if not any(ln.lstrip().startswith("|") for ln in content.splitlines()):
            continue  # 尚无表格形态，不强行校验
        try:
            cols, _rows = parse_table(content, need[0])
        except Exception:
            continue
        if not cols:
            problems.append(
                f"「{sec}」已有表格但表头不含「{need[0]}」（应为 {'｜'.join(need)}）")
            continue
        missing = [c for c in need if c not in cols]
        if missing:
            problems.append(f"「{sec}」表头缺列：{'｜'.join(missing)}（应为 {'｜'.join(need)}）")
    return problems


def write(book: str, sections: dict[str, str]) -> None:
    """整本写回：保留固定节顺序，未知节追加尾部。

    写回前校验固定表格节的表头（规格01 §2.2）：列名漂移会让
    power_rows()/realm_order()/terms() 静默返回空，下游的境界倒退检测、
    术语一致性比对随之失效——这类"不报错的失效"比崩溃更难发现。
    """
    problems = validate_sections(sections)
    if problems:
        raise BibleError("；".join(problems))
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
