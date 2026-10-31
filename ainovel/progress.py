"""（规格07 §5.3）书架与总览共用的文件真相进度派生。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from . import chapters, mechcheck
from .navigation import planned_numbers
from .paths import book_dir


def _warning(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _content_paths(book: str) -> list[Path]:
    root = book_dir(book)
    paths = [root / name for name in (
        "meta.md", "config.json", "STORY_BIBLE.md", "OUTLINE.md",
        "PLOT_POINTS.md", "LOCATIONS.md", "TIMELINE.md",
    )]
    paths.extend((root / "style" / name) for name in ("语料.txt", "特征卡.md", "范例.json"))
    chars = root / "characters"
    if chars.is_dir():
        paths.extend(chars.glob("*.md"))
    chapter_dir = root / "chapters"
    if chapter_dir.is_dir():
        paths.extend(path for path in chapter_dir.glob("*.md")
                     if path.name.endswith(("_细纲.md", "_正文.md"))
                     and path.name.split("_", 1)[0].isdigit())
    return paths


def _last_edited(book: str, warnings: list[dict[str, str]]) -> str | None:
    latest: float | None = None
    for path in _content_paths(book):
        try:
            if path.is_file():
                mtime = path.stat().st_mtime
                latest = mtime if latest is None else max(latest, mtime)
        except OSError as error:
            warnings.append(_warning("EDIT_TIME_UNKNOWN", f"无法读取内容时间 {path.name}：{error}"))
    if latest is None:
        return None
    return datetime.fromtimestamp(latest).astimezone().isoformat()


def derive(book: str) -> dict[str, Any]:
    """损坏统计返回 null + warning，绝不把未知静默当成零。"""
    warnings: list[dict[str, str]] = []
    planned, planning_error = planned_numbers(book)
    total = None if planned is None else len(planned)
    if planning_error:
        warnings.append(planning_error)

    done: int | None = 0
    words: int | None = 0
    for no in chapters.list_chapters(book):
        path = chapters.prose_path(book, no)
        if not path.is_file():
            continue
        try:
            prose = chapters.read_prose(book, no)
            if prose.get("status") == "final":
                done += 1
                words += mechcheck.count_words(prose["_body"])
        except Exception as error:
            done = None
            words = None
            warnings.append(_warning("PROSE_INVALID", f"第{no}章正文读取或校验失败：{error}"))
            break
    return {
        "chapters_done": done,
        "chapters_total": total,
        "words_done": words,
        "last_edited_at": _last_edited(book, warnings),
        "progress_warnings": warnings,
    }
