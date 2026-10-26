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
        # 可选：开篇承接点（老细纲没有这个字段，只能按可选处理）
        "opening_bridge": (str, type(None)),
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

MEMORY_SCHEMA_V1: FM.Schema = {
    "required": ["chapter_no", "char_updates", "new_chars", "new_locations",
                 "foreshadow_ops", "event_summary", "time_elapsed"],
    "types": {
        "chapter_no": int, "char_updates": list, "new_chars": list,
        "new_locations": list, "foreshadow_ops": list,
        "new_settings_pending": (list, type(None)),
        "event_summary": str, "time_elapsed": str,
    },
}

# 规格05 F-05：新增结构化增量。**必须允许老卡通过**——
# Bible 表头校验误伤过整本书，同样的错不能在记忆卡上重犯。
MEMORY_SCHEMA: FM.Schema = {
    "required": ["chapter_no", "char_updates", "new_chars", "new_locations",
                 "foreshadow_ops", "event_summary", "time_elapsed", "state_delta"],
    "types": {
        "chapter_no": int, "char_updates": list, "new_chars": list,
        "new_locations": list, "foreshadow_ops": list,
        "new_settings_pending": (list, type(None)),
        "event_summary": str, "time_elapsed": str,
        "state_delta": list,
        "timeline_candidates": (list, type(None)),
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
    _mark_check_stale(book, no, body)


def _mark_check_stale(book: str, no: int, body: str) -> None:
    """正文一变，旧机检结论即过期（规格05 P-3 / §2.6）。

    单点放在 write_prose 里：所有写正文的路径（生成、手改、定向修复、润色、
    订正、采纳草稿）都必须经过这里，避免漏掉某条路径导致"正文已改、结论仍绿"。
    指纹相同（如定稿时原样写回）不会误标 stale。
    """
    import logging

    from . import db, mechcheck

    try:
        db.mark_check_stale(book_id=books.book_id(book), chapter_no=no,
                            current_fingerprint=mechcheck.fingerprint(body))
    except Exception as e:  # 标记失败不能回滚已落盘的正文，但必须留痕
        logging.getLogger(__name__).warning("第%s章机检结论过期标记失败: %s", no, e)


# ---------------------------------------------------------------------------
# 半成品草稿（规格03 §5：generating 中断 → 保留半成品草稿为 draft.vN）
# ---------------------------------------------------------------------------

def draft_path(book: str, no: int):
    """半成品草稿路径：与正文同目录，不参与章节扫描（文件名不以数字+下划线开头）。"""
    return chapters_dir(book) / f"{no:03d}_正文.draft.md"


def write_draft(book: str, no: int, text: str) -> None:
    """流式生成途中周期性落盘半成品，崩溃后可供"继续/重来"判断，不覆盖正式正文。"""
    d = chapters_dir(book)
    d.mkdir(parents=True, exist_ok=True)
    atomic.write_text(draft_path(book, no), text)


def read_draft(book: str, no: int) -> str:
    p = draft_path(book, no)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def clear_draft(book: str, no: int) -> None:
    p = draft_path(book, no)
    if p.exists():
        p.unlink()


def count_words(body: str) -> int:
    """中文字数惯例：非空白字符数。"""
    return len(re.sub(r"\s+", "", body))


# ---------------------------------------------------------------------------
# 记忆卡
# ---------------------------------------------------------------------------

def memory_fingerprint(book: str, no: int) -> str | None:
    """记忆卡记录的正文指纹（老卡没有该字段 → None，不代表不匹配）。"""
    try:
        fm, _ = FM.read_file(memory_path(book, no))
    except (FileNotFoundError, FM.FrontmatterError):
        return None
    fp = fm.get("source_fingerprint")
    return str(fp) if fp else None


def clear_memory(book: str, no: int) -> None:
    p = memory_path(book, no)
    if p.exists():
        p.unlink()


def read_memory(book: str, no: int) -> dict[str, Any]:
    """读记忆卡：先按 V2（含 state_delta）校验，失败回落 V1 并标 `legacy=True`。

    老卡不报错（这是硬要求）：历史章节没有 state_delta，若直接抛错，
    整本书的装配与补做都会卡死。`legacy=True` 让调用方知道"这卡没有结构化状态"。
    """
    fm, body = FM.read_file(memory_path(book, no))
    legacy = False
    try:
        FM.validate(fm, MEMORY_SCHEMA)
    except FM.FrontmatterError:
        FM.validate(fm, MEMORY_SCHEMA_V1)
        legacy = True
    return fm | {"_body": body, "legacy": legacy}


def validate_memory(fm: dict[str, Any]) -> bool:
    """校验记忆卡字典；返回是否为 legacy（缺 state_delta）。失败抛 FrontmatterError。"""
    try:
        FM.validate(fm, MEMORY_SCHEMA)
        return False
    except FM.FrontmatterError:
        FM.validate(fm, MEMORY_SCHEMA_V1)
        return True


def write_memory(book: str, no: int, fm: dict[str, Any], body: str) -> None:
    if fm.get("chapter_no") != no:
        raise FM.FrontmatterError(f"记忆卡 chapter_no={fm.get('chapter_no')} 与文件章号 {no} 不一致")
    validate_memory(fm)
    atomic.write_text(memory_path(book, no), FM.dump(fm, body))
