"""chapters/ 三件套：细纲（章合同）/ 正文 / 记忆卡（规格01 §2.7-§2.9、规格02 P-G6/P-V4）。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import atomic, books, frontmatter as FM

OUTLINE_CONTRACT_SCHEMA: FM.Schema = {
    "required": ["chapter_no", "volume", "stage", "emotion_task", "core_event",
                 "scenes", "emotion_arc", "hook", "word_range", "pov"],
    "non_empty": ["core_event", "pov"],
    "types": {
        "chapter_no": int, "volume": int, "stage": str, "emotion_task": str,
        "purpose": (list, type(None)), "core_event": str, "scenes": list,
        "emotion_arc": dict, "must_include": (list, type(None)),
        "hook": dict, "foreshadow_ops": (list, type(None)),
        "word_range": list, "pov": str, "confirmed": bool,
    },
    "enums": {
        "stage": ["引入", "发展", "转折", "高潮", "收束"],
        "emotion_task": ["压", "小扬", "爆"],
    },
}

PROSE_SCHEMA: FM.Schema = {
    "required": ["chapter_no", "version", "status", "word_count"],
    "types": {"chapter_no": int, "version": int, "word_count": int,
              "finalized_at": (str, type(None))},
    "enums": {"status": ["draft", "final"]},
}

MEMORY_SCHEMA: FM.Schema = {
    "required": ["chapter_no", "char_updates", "new_chars", "new_locations",
                 "foreshadow_ops", "event_summary", "time_elapsed"],
    "types": {
        "chapter_no": int, "char_updates": list, "new_chars": list,
        "new_locations": list, "foreshadow_ops": list,
        "new_settings_pending": (list, type(None)),
        "event_summary": str, "time_elapsed": str,
    },
}


def chapters_dir(book: str) -> Path:
    return books.book_dir(book) / "chapters"


def outline_path(book: str, no: int) -> Path:
    return chapters_dir(book) / f"{no:03d}_细纲.md"


def prose_path(book: str, no: int) -> Path:
    return chapters_dir(book) / f"{no:03d}_正文.md"


def memory_path(book: str, no: int) -> Path:
    return chapters_dir(book) / f"{no:03d}_记忆卡.md"


def list_chapters(book: str) -> list[int]:
    d = chapters_dir(book)
    if not d.exists():
        return []
    nos = set()
    for f in d.glob("*.md"):
        try:
            nos.add(int(f.name.split("_", 1)[0]))
        except ValueError:
            continue
    return sorted(nos)


# ---------------------------------------------------------------------------
# 细纲（章合同）
# ---------------------------------------------------------------------------

def read_outline_contract(book: str, no: int) -> dict[str, Any]:
    fm, body = FM.validate_file(outline_path(book, no), OUTLINE_CONTRACT_SCHEMA)
    if not isinstance(fm.get("hook"), dict) or "type" not in fm["hook"]:
        raise FM.FrontmatterError(f"第{no}章细纲 hook 缺少 type 字段")
    return fm | {"_body": body}


def write_outline_contract(book: str, no: int, fm: dict[str, Any], body: str) -> None:
    if fm.get("chapter_no") != no:
        raise FM.FrontmatterError(f"细纲 chapter_no={fm.get('chapter_no')} 与文件章号 {no} 不一致")
    FM.validate(fm, OUTLINE_CONTRACT_SCHEMA)
    fm.setdefault("confirmed", False)
    atomic.write_text(outline_path(book, no), FM.dump(fm, body))


def confirm_outline(book: str, no: int) -> None:
    fm = read_outline_contract(book, no)
    body = fm.pop("_body")
    fm["confirmed"] = True
    write_outline_contract(book, no, fm, body)


# ---------------------------------------------------------------------------
# 正文
# ---------------------------------------------------------------------------

def read_prose(book: str, no: int) -> dict[str, Any]:
    fm, body = FM.validate_file(prose_path(book, no), PROSE_SCHEMA)
    return fm | {"_body": body}


def write_prose(book: str, no: int, fm: dict[str, Any], body: str) -> None:
    if fm.get("chapter_no") != no:
        raise FM.FrontmatterError(f"正文 chapter_no={fm.get('chapter_no')} 与文件章号 {no} 不一致")
    FM.validate(fm, PROSE_SCHEMA)
    atomic.write_text(prose_path(book, no), FM.dump(fm, body))


def count_words(body: str) -> int:
    """中文字数惯例：非空白字符数。"""
    return len(re.sub(r"\s+", "", body))


# ---------------------------------------------------------------------------
# 记忆卡
# ---------------------------------------------------------------------------

def read_memory(book: str, no: int) -> dict[str, Any]:
    fm, body = FM.validate_file(memory_path(book, no), MEMORY_SCHEMA)
    return fm | {"_body": body}


def write_memory(book: str, no: int, fm: dict[str, Any], body: str) -> None:
    if fm.get("chapter_no") != no:
        raise FM.FrontmatterError(f"记忆卡 chapter_no={fm.get('chapter_no')} 与文件章号 {no} 不一致")
    FM.validate(fm, MEMORY_SCHEMA)
    atomic.write_text(memory_path(book, no), FM.dump(fm, body))
