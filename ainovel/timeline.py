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
from .operations import serialized

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


@serialized
def write(book: str, truth: list[dict[str, str]],
          released: list[dict[str, str]] | None = None) -> None:
    if released is None:
        released = read(book)["released"]
    for rows, columns in ((truth, TRUTH_COLS), (released, RELEASED_COLS)):
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("时间线行必须是对象")
            for column in columns:
                cell = row.get(column, "")
                if not isinstance(cell, str) or any(char in cell for char in ("\r", "\n", "|")):
                    raise ValueError(f"时间线 {column} 必须是单行文本且不得含竖线分隔符")
    text = (_HEADER
            + "## 一、作者真相（故事世界实际发生）\n\n"
            + dump_table(TRUTH_COLS, [[r.get(c, "") for c in TRUTH_COLS] for r in truth])
            + "\n\n## 二、读者已知（按章释放）\n\n"
            + dump_table(RELEASED_COLS, [[r.get(c, "") for c in RELEASED_COLS] for r in released])
            + "\n")
    issues = validate(book, data={"exists": True, "truth": truth, "released": released, "raw": text})
    if any(i["level"] == "block" for i in issues):
        raise ValueError("时间线校验失败：" + "；".join(i["message"] for i in issues[:8]))
    atomic.write_text(timeline_path(book), text)


def _line_of(raw: str, needle: str) -> int:
    for i, line in enumerate(raw.splitlines(), 1):
        if needle in line:
            return i
    return 0


def validate(book: str, body: str = "", *, data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """（规格05 F-06）M-16/M-17 校验；候选表也可在写盘前验证。"""
    data = read(book) if data is None else data
    if not data["exists"]:
        return []
    raw = data["raw"]
    known = set(characters.list_characters(book))
    hits: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for group, columns in (("truth", TRUTH_COLS), ("released", RELEASED_COLS)):
        for row in data.get(group, []):
            if not isinstance(row, dict) or any(not isinstance(row.get(col, ""), str) for col in columns):
                return [{"rule": "M-16", "level": "block", "line": 0, "word": "",
                         "message": f"{group} 每个单元格必须为文本"}]

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

    for row in data.get("released", []):
        chapter = row.get("章", "").strip()
        refs = [ref for ref in re.split(r"[,，、;；\s]+", row.get("本章向读者释放的真相", "")) if ref]
        if not chapter.isdigit() or int(chapter) < 1 or not refs:
            hits.append({"rule": "M-16", "level": "block", "line": 0, "word": chapter,
                         "message": "读者已知表必须填写合法章号与真相编号"})
        for ref in refs:
            if ref not in seen:
                hits.append({"rule": "M-16", "level": "block", "line": 0, "word": ref,
                             "message": f"读者已知表引用不存在的编号：{ref}"})
    # M-17 悬空引用（正文 / 细纲提到 T-xxx 但表里没有）
    if body:
        for ref in set(REF_RE.findall(body)):
            if ref not in seen:
                hits.append({"rule": "M-17", "level": "warn", "line": 0, "word": ref,
                             "message": f"正文引用了时间线不存在的编号：{ref}"})
    return hits


def _split_names(cell: str) -> list[str]:
    return [n.strip() for n in re.split(r"[,，、;；]", cell or "") if n.strip()]


MAX_CONSTRAINT_EVENTS = 8


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
    # 主视角角色在「读者已知=是」的事件上一律视为知情：读者是通过他看到的，
    # 再去禁止他提及只会写出「主角回避自己刚发现的事」这种别扭戏。
    pov = str(contract.get("pov") or "").strip()
    if not present:
        return "（本章出场角色未知，未注入时间线约束）"

    # 按「事件」合并同一批人，而不是一人一行：18 条时间线 × 2 个角色 = 36 行，
    # 塞进上下文只会被整体截断，模型一条都没读到（实测截断在第十条）。
    order: list[tuple[str, bool]] = []
    events: dict[str, str] = {}
    blocked: dict[tuple[str, bool], list[str]] = {}
    for row in data["truth"]:
        tid = (row.get("编号") or "").strip()
        event = (row.get("事件") or "").strip()
        if not tid or not event:
            continue
        informed = _split_names(row.get("知情角色", ""))
        reader_known = (row.get("读者已知") or "").strip() == "是"
        key = (tid, reader_known)
        events[tid] = event
        for name in present:
            if name in informed:
                continue
            if reader_known and name == pov:
                continue
            if key not in blocked:
                blocked[key] = []
                order.append(key)
            blocked[key].append(name)

    lines: list[str] = []
    for key in order[:MAX_CONSTRAINT_EVENTS]:
        tid, reader_known = key
        names = blocked[key]
        tail = ("不得让其提及或指向" if reader_known
                else "连暗示都要克制，不能点明")
        lines.append(f"- {tid}「{events[tid][:40]}」：{'、'.join(names)} {tail}。")
    if not lines:
        return "（本章出场角色均无知情禁区）"
    text = "\n".join(lines)
    if len(order) > MAX_CONSTRAINT_EVENTS:
        text += f"\n（另有 {len(order) - MAX_CONSTRAINT_EVENTS} 条禁区未列出，写作时同样成立）"
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
