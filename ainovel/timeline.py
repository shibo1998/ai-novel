"""双时间线（规格05 F-06）：区分「故事世界真相」与「读者/角色目前知道什么」。

长篇最隐蔽的硬伤不是错别字，是**角色说出他不可能知道的事**，或者伏笔提前泄底。
这份文件是书的骨架真相，所以**不由 LLM 自由生成**：V4 只产出候选，人工确认后才落盘
（自动写入会污染证据链，同 M-04 敏感词禁止自动改写的道理）。
"""
from __future__ import annotations

import re
from typing import Any

from . import atomic, books, chapters, characters
from .mdtable import dump_table, parse_table
from .paths import book_dir

TIMELINE_FILE = "TIMELINE.md"
TRUTH_COLS = ["编号", "章", "事件", "涉及角色", "读者已知", "知情角色"]
RELEASED_COLS = ["章", "本章向读者释放的真相"]
ID_RE = re.compile(r"^T-\d{3}$")
REF_RE = re.compile(r"T-\d{3}")

_HEADER = """# 时间线

<!-- 由人工维护：V4 只产候选，确认后才写这里。字段：编号 T-001 / 章 / 读者已知（是|否） -->

"""


def timeline_path(book: str):
    return book_dir(book) / TIMELINE_FILE


def read(book: str) -> dict[str, Any]:
    p = timeline_path(book)
    if not p.exists():
        return {"exists": False, "truth": [], "released": [], "raw": ""}
    raw = p.read_text(encoding="utf-8")
    _, truth = parse_table(raw, "编号")
    _, released = parse_table(raw, "本章向读者释放的真相")
    return {"exists": True, "truth": truth, "released": released, "raw": raw}


def write(book: str, truth: list[dict[str, str]],
          released: list[dict[str, str]] | None = None) -> None:
    if released is None:
        released = read(book)["released"]
    text = (_HEADER
            + "## 一、作者真相（故事世界实际发生）\n\n"
            + dump_table(TRUTH_COLS, [[r.get(c, "") for c in TRUTH_COLS] for r in truth])
            + "\n\n## 二、读者已知（按章释放）\n\n"
            + dump_table(RELEASED_COLS, [[r.get(c, "") for c in RELEASED_COLS] for r in released])
            + "\n")
    atomic.write_text(timeline_path(book), text)


def _line_of(raw: str, needle: str) -> int:
    for i, line in enumerate(raw.splitlines(), 1):
        if needle in line:
            return i
    return 0


def validate(book: str, body: str = "") -> list[dict[str, Any]]:
    """M-16 格式 / M-17 悬空引用。返回可直接转成 mechcheck.Hit 的 dict。"""
    data = read(book)
    if not data["exists"]:
        return []
    raw = data["raw"]
    known = set(characters.list_characters(book))
    hits: list[dict[str, Any]] = []
    seen: dict[str, int] = {}

    for row in data["truth"]:
        tid = (row.get("编号") or "").strip()
        line = _line_of(raw, tid) if tid else 0
        if not tid:
            hits.append({"rule": "M-16", "level": "block", "line": 0, "word": "",
                         "message": "时间线存在空编号行"})
            continue
        if not ID_RE.match(tid):
            hits.append({"rule": "M-16", "level": "block", "line": line, "word": tid,
                         "message": f"编号格式非法（应为 T-000）：{tid}"})
        if tid in seen:
            hits.append({"rule": "M-16", "level": "block", "line": line, "word": tid,
                         "message": f"编号重复：{tid}（首次出现在第 {seen[tid]} 行）"})
        else:
            seen[tid] = line
        ch = (row.get("章") or "").strip()
        if not ch.isdigit() or int(ch) < 1:
            hits.append({"rule": "M-16", "level": "block", "line": line, "word": ch,
                         "message": f"{tid} 的章号非法：{ch!r}"})
        known_flag = (row.get("读者已知") or "").strip()
        if known_flag not in ("是", "否"):
            hits.append({"rule": "M-16", "level": "block", "line": line, "word": known_flag,
                         "message": f"{tid} 的『读者已知』必须是 是/否，实际 {known_flag!r}"})
        if not (row.get("事件") or "").strip():
            hits.append({"rule": "M-16", "level": "block", "line": line, "word": tid,
                         "message": f"{tid} 缺事件描述"})
        for name in _split_names(row.get("知情角色", "")):
            if name not in known:
                hits.append({"rule": "M-16", "level": "block", "line": line, "word": name,
                             "message": f"{tid} 的知情角色 {name} 不在 characters/ 中"})

    # M-17 悬空引用（正文 / 细纲提到 T-xxx 但表里没有）
    if body:
        for ref in set(REF_RE.findall(body)):
            if ref not in seen:
                hits.append({"rule": "M-17", "level": "warn", "line": 0, "word": ref,
                             "message": f"正文引用了时间线不存在的编号：{ref}"})
    return hits


def _split_names(cell: str) -> list[str]:
    return [n.strip() for n in re.split(r"[,，、;；]", cell or "") if n.strip()]


def constraints_for_chapter(book: str, chapter_no: int, limit: int = 800) -> str:
    """本章出场角色的知情禁区（只注入相关条目，不注入全表）。"""
    data = read(book)
    if not data["exists"] or not data["truth"]:
        return "（未建立时间线）"
    try:
        contract = chapters.read_outline_contract(book, chapter_no)
    except Exception:
        contract = {}
    present: list[str] = []
    for scene in contract.get("scenes") or []:
        for c in scene.get("chars") or []:
            name = c.get("name", "")
            if name and name not in present:
                present.append(name)
    if not present:
        return "（本章出场角色未知，未注入时间线约束）"

    lines: list[str] = []
    for row in data["truth"]:
        tid = (row.get("编号") or "").strip()
        event = (row.get("事件") or "").strip()
        if not tid or not event:
            continue
        informed = _split_names(row.get("知情角色", ""))
        reader_known = (row.get("读者已知") or "").strip() == "是"
        for name in present:
            if name in informed:
                continue
            if reader_known:
                lines.append(f"- {tid}「{event}」：{name} 不知情，不得让其提及或指向该事件。")
            else:
                lines.append(f"- {tid}「{event}」：{name} 不知情，且读者也未知——只能暗示，不能点明。")
    if not lines:
        return "（本章出场角色均无知情禁区）"
    text = "\n".join(lines)
    return text[:limit] + ("\n（……已截断……）" if len(text) > limit else "")


def candidates(book: str, items: list[dict[str, Any]], chapter_no: int) -> list[Any]:
    """把 V4 的时间线候选转成裁决队列条目（**不写 TIMELINE.md**）。

    理由：时间线是书的骨架真相，自动落盘会污染证据链。人工在裁决页确认后
    才由 `conflicts._apply("timeline_candidate")` 调 `append_truth` 写进来。
    """
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        event = (it.get("event") or "").strip()
        if not event:
            continue
        out.append(characters.Conflict(
            "timeline_candidate", book, chapter_no, event, None,
            {"characters": it.get("characters") or [],
             "reader_known": bool(it.get("reader_known"))},
        ))
    return out


def append_truth(book: str, event: str, characters_in: list[str], chapter_no: int,
                 reader_known: bool) -> dict[str, str]:
    """人工确认后落盘一条真相（编号自动取下一个 T-xxx）。"""
    data = read(book)
    used = {int(r["编号"][2:]) for r in data["truth"] if ID_RE.match(r.get("编号", ""))}
    nxt = 1
    while nxt in used:
        nxt += 1
    row = {"编号": f"T-{nxt:03d}", "章": str(chapter_no), "事件": event,
           "涉及角色": "、".join(characters_in),
           "读者已知": "是" if reader_known else "否",
           "知情角色": "、".join(characters_in)}
    write(book, data["truth"] + [row], data["released"])
    return row
