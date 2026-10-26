"""人工裁决队列：台账/档案冲突持久化 + 解决动作（规格01 §4、规格02 P-V4）。

队列非空时禁止开下一章细纲（强制清空）——由 pipeline 调用 guard() 强制。
"""
from __future__ import annotations

import json
import threading
from typing import Any

from . import atomic, bible, characters, db
from .paths import data_dir

_LOCK = threading.Lock()


def _path() -> "Any":
    return data_dir() / "conflicts.json"


def _load() -> list[dict[str, Any]]:
    p = _path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("items", [])
    except (json.JSONDecodeError, OSError):
        return []


def _save(items: list[dict[str, Any]]) -> None:
    data_dir().mkdir(parents=True, exist_ok=True)
    atomic.write_text(_path(), json.dumps({"items": items}, ensure_ascii=False, indent=2))


def enqueue(conflicts: list[characters.Conflict]) -> int:
    if not conflicts:
        return 0
    with _LOCK:
        items = _load()
        for c in conflicts:
            d = c.to_dict()
            d["id"] = _next_id(items)
            d["created_at"] = db.now()
            d["resolved"] = False
            items.append(d)
        _save(items)
    return len(conflicts)


def _next_id(items: list[dict[str, Any]]) -> int:
    return max((it.get("id", 0) for it in items), default=0) + 1


def list_open(book: str | None = None) -> list[dict[str, Any]]:
    items = [it for it in _load() if not it.get("resolved")]
    if book:
        items = [it for it in items if it.get("book") == book]
    return items


def resolve(conflict_id: int, action: str, manual_value: Any = None) -> dict[str, Any]:
    """action: adopt_ai（采纳新值）/ keep_archive（保留档案）/ manual（手改值）。"""
    if action not in ("adopt_ai", "keep_archive", "manual"):
        raise ValueError(f"非法裁决动作: {action}")
    with _LOCK:
        items = _load()
        target = next((it for it in items if it.get("id") == conflict_id and not it.get("resolved")), None)
        if target is None:
            raise KeyError(f"冲突不存在或已解决: {conflict_id}")
        value = manual_value if action == "manual" else target.get("new")
        note = ""
        # new_setting 不依赖 new 值（详情在 detail 里），采纳/手改都要执行写回
        if action != "keep_archive" and (value is not None or target.get("kind") == "new_setting"):
            note = _apply(target, value)
        target["resolved"] = True
        target["resolution"] = {"action": action, "value": value, "note": note, "at": db.now()}
        _save(items)
    return target


def _apply(item: dict[str, Any], value: Any) -> str:
    kind = item.get("kind")
    book = item.get("book", "")
    char = item.get("char", "")
    if kind == "timeline_candidate":
        # 时间线候选只有人工裁决才落盘（规格05 §7.3：LLM 自由生成会污染骨架真相）
        from . import timeline

        extra = value if isinstance(value, dict) else {}
        row = timeline.append_truth(
            book, item.get("detail", ""), extra.get("characters") or [],
            int(item.get("chapter_no") or 1), bool(extra.get("reader_known")))
        return f"已写入时间线 {row['编号']}"
    if kind == "new_setting" and not char:
        # 采纳 → 自动补录进 STORY_BIBLE 术语表（term 取"待补录设定："后的第一个冒号前段）
        detail = item.get("detail", "")
        rest = detail.split("：", 1)[-1] if "：" in detail else detail
        term = rest.split("：", 1)[0].split("（", 1)[0].strip() or "未命名设定"
        desc = rest.split("：", 1)[1].strip() if "：" in rest else rest
        try:
            sections = bible.read(book)
            terms = sections.get("术语表", "")
            if term not in terms:
                sections["术语表"] = (terms.rstrip() + f"\n| {term} | {desc} |").strip()
                bible.write(book, sections)
                return f"已补录术语「{term}」"
            return f"术语「{term}」已存在"
        except Exception as e:
            return f"术语补录失败（{e}），仅记录裁决"
    if kind == "missing_char" and isinstance(value, dict):
        # value: {identity, stance, role}
        characters.create(book, char, role=value.get("role", "龙套"))
        return f"已建档角色 {char}"
    if char and item.get("field"):
        data = characters.read(book, char)
        status = dict(data.get("status") or {})
        status[item["field"]] = value
        status["updated_chapter"] = item.get("chapter_no", status.get("updated_chapter", 0))
        fm = {k: v for k, v in data.items() if not k.startswith("_")}
        fm["status"] = status
        characters.write(book, fm, data["_body"])
        return f"已写回 {char}.{item['field']} = {value!r}"
    return "无自动写回目标（仅记录裁决）"


def guard(book: str) -> None:
    """台账冲突队列非空 → 禁止开始下一章细纲生成（规格01 §4）。"""
    open_items = list_open(book)
    if open_items:
        raise RuntimeError(
            f"裁决队列非空（{len(open_items)} 条待处理），禁止开新章细纲；请先在裁决页清空"
        )
