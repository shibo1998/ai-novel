"""人工裁决队列（规格01 §4、规格02 P-V4、规格07 §5.4）。

写回目标并核对成功后才能保存 resolved 回执；目标已写而回执失败时保留待处理项，
人工重试按预期结果幂等补记，不重复追加。锁顺序固定为书锁 → 队列锁。
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any

from . import atomic, bible, characters, db
from .operations import writing
from .paths import data_dir

_LOCK = threading.RLock()
_WRITE_ONLY_KINDS = {"new_setting", "timeline_candidate", "missing_char",
                     "dead_reappear", "realm_regress"}
_INFO_ONLY_KINDS = {"unknown_field", "realm_name", "foreshadow_op"}


class ConflictError(RuntimeError):
    """裁决契约错误，携带稳定 HTTP 状态与错误码。"""

    def __init__(self, code: str, message: str, http_status: int):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class ConflictValueInvalid(ConflictError):
    def __init__(self, message: str):
        super().__init__("CONFLICT_VALUE_INVALID", message, 422)


class ConflictActionUnavailable(ConflictError):
    def __init__(self, message: str):
        super().__init__("CONFLICT_ACTION_UNAVAILABLE", message, 400)


class ConflictChanged(ConflictError):
    def __init__(self, message: str = "目标档案已变化，请刷新冲突后重新判断"):
        super().__init__("CONFLICT_CHANGED", message, 409)


class ConflictWriteError(ConflictError):
    def __init__(self, message: str):
        super().__init__("CONFLICT_WRITE_FAILED", message, 500)


class ConflictReceiptError(ConflictError):
    def __init__(self, message: str):
        super().__init__("CONFLICT_RECEIPT_FAILED", message, 500)


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


def _manual_schema(item: dict[str, Any]) -> str | None:
    kind = item.get("kind")
    if kind in ("new_setting", "timeline_candidate", "missing_char"):
        return str(kind)
    if kind == "unknown_field" or not item.get("field"):
        return None
    field = item.get("field")
    if field == "alive":
        return "boolean"
    if field in ("injuries", "inventory", "known_info"):
        return "string_list"
    if field == "updated_chapter":
        return "integer"
    if field in characters.STATUS_FIELDS:
        return "text"
    return None


def _adoptable(item: dict[str, Any]) -> bool:
    kind = item.get("kind")
    if kind == "new_setting":
        try:
            _setting_value(item, None)
            return True
        except ConflictError:
            return False
    if kind == "timeline_candidate":
        return bool(str(item.get("detail") or "").strip()
                    and type(item.get("chapter_no")) is int and item.get("chapter_no") > 0)
    if kind == "missing_char":
        return bool(str(item.get("char") or "").strip() and isinstance(item.get("new"), dict))
    if kind in _INFO_ONLY_KINDS:
        return False
    return bool(item.get("char") and item.get("field") in characters.STATUS_FIELDS
                and item.get("new") is not None)


def _capabilities(item: dict[str, Any]) -> dict[str, Any]:
    allowed = ["keep_archive"]
    if _adoptable(item):
        allowed.insert(0, "adopt_ai")
    if _manual_schema(item) is not None:
        allowed.insert(-1, "manual")
    return {"allowed_actions": allowed, "manual_schema": _manual_schema(item)}


def list_open(book: str | None = None) -> list[dict[str, Any]]:
    items = [it for it in _load() if not it.get("resolved")]
    if book:
        items = [it for it in items if it.get("book") == book]
    return [it | _capabilities(it) for it in items]


def resolve(conflict_id: int, action: str, manual_value: Any = None) -> dict[str, Any]:
    """（规格07 UI-29）人工决定动作；真实写回、验证、回执严格按此顺序。"""
    if action not in ("adopt_ai", "keep_archive", "manual"):
        raise ConflictActionUnavailable(f"非法裁决动作: {action}")
    with _LOCK:
        existing = next((it for it in _load() if it.get("id") == conflict_id), None)
    if existing is None:
        raise KeyError(f"冲突不存在: {conflict_id}")
    book = str(existing.get("book") or "")
    if not book:
        raise ConflictValueInvalid("旧冲突缺少书名，无法安全写回")
    with writing(book):
        with _LOCK:
            items = _load()
            target = next((it for it in items if it.get("id") == conflict_id), None)
            if target is None:
                raise KeyError(f"冲突不存在: {conflict_id}")
            if target.get("resolved"):
                raise ConflictChanged("冲突已解决，请刷新队列")
            capabilities = _capabilities(target)
            if action not in capabilities["allowed_actions"]:
                raise ConflictActionUnavailable(
                    f"该冲突不支持动作 {action}；候选不足或该类型只允许人工保留/确认")
            effect = "kept"
            value = manual_value if action == "manual" else target.get("new")
            note = "人工选择保留档案，不采用候选"
            if action == "keep_archive" and not _adoptable(target) and _manual_schema(target) is None:
                effect = "acknowledged"
                note = "人工确认已处理/不采用说明，不执行自动写回"
            elif action != "keep_archive":
                try:
                    note = _apply(target, value, manual=action == "manual")
                except ConflictError:
                    raise
                except Exception as error:
                    raise ConflictWriteError(f"写回目标失败，冲突仍待处理：{error}") from error
                effect = "applied"
            target["resolved"] = True
            target["resolution"] = {"action": action, "value": value, "note": note,
                                    "effect": effect, "at": db.now()}
            target["effect"] = effect
            try:
                _save(items)
            except Exception as error:
                raise ConflictReceiptError(
                    f"目标可能已写入，但裁决回执保存失败，需核对结果后重试：{error}") from error
    return target


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConflictValueInvalid(f"{label}必须是非空文本")
    return value.strip()


def _setting_value(item: dict[str, Any], value: Any) -> dict[str, str]:
    if value is None:
        detail = str(item.get("detail") or "")
        rest = detail.split("：", 1)[-1] if "：" in detail else detail
        parts = rest.split("：", 1)
        value = {"term": parts[0].split("（", 1)[0],
                 "description": parts[1] if len(parts) == 2 else ""}
    if not isinstance(value, dict):
        raise ConflictValueInvalid("术语人工值必须是 {term, description} 对象")
    return {"term": _required_text(value.get("term"), "term"),
            "description": _required_text(value.get("description"), "description")}


def _timeline_value(item: dict[str, Any], value: Any, manual: bool) -> dict[str, Any]:
    if not manual:
        extra = value if isinstance(value, dict) else {}
        value = {"chapter_no": item.get("chapter_no"), "event": item.get("detail"),
                 "characters": extra.get("characters") or [],
                 "reader_known": extra.get("reader_known", False)}
    if not isinstance(value, dict):
        raise ConflictValueInvalid("时间线人工值必须是对象")
    no = value.get("chapter_no")
    if type(no) is not int or no < 1:
        raise ConflictValueInvalid("chapter_no 必须是正整数")
    chars = value.get("characters")
    if not isinstance(chars, list) or any(not isinstance(v, str) for v in chars):
        raise ConflictValueInvalid("characters 必须是字符串数组")
    if type(value.get("reader_known")) is not bool:
        raise ConflictValueInvalid("reader_known 必须是布尔值")
    return {"chapter_no": no, "event": _required_text(value.get("event"), "event"),
            "characters": chars, "reader_known": value["reader_known"]}


def _status_value(field: str, value: Any) -> Any:
    if field == "alive" and type(value) is not bool:
        raise ConflictValueInvalid("alive 必须是布尔值，不接受字符串 false/true")
    if field in ("injuries", "inventory", "known_info"):
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            raise ConflictValueInvalid(f"{field} 必须是字符串数组")
    if field == "updated_chapter" and (type(value) is not int or value < 0):
        raise ConflictValueInvalid("updated_chapter 必须是非负整数")
    if field in ("realm", "location", "emotion") and not isinstance(value, str):
        raise ConflictValueInvalid(f"{field} 必须是文本")
    if field not in characters.STATUS_FIELDS:
        raise ConflictActionUnavailable(f"未知状态字段 {field} 不提供通用写回")
    return value


def _missing_char_value(item: dict[str, Any], value: Any, manual: bool) -> dict[str, Any]:
    if not manual:
        value = {"name": item.get("char"), "role": "龙套", "status": value}
    if not isinstance(value, dict):
        raise ConflictValueInvalid("缺档角色人工值必须是 {name, role, status} 对象")
    name = _required_text(value.get("name"), "name")
    role = value.get("role", "龙套")
    if role not in ("主角", "女主", "反派", "重要配角", "龙套"):
        raise ConflictValueInvalid("role 必须是主角/女主/反派/重要配角/龙套之一")
    status = value.get("status")
    if not isinstance(status, dict):
        raise ConflictValueInvalid("status 必须是对象；旧条目缺状态时请人工补齐")
    clean = {field: _status_value(field, val) for field, val in status.items()}
    return {"name": name, "role": role, "status": clean}


def _apply(item: dict[str, Any], value: Any, *, manual: bool) -> str:
    kind = item.get("kind")
    book = str(item.get("book") or "")
    if kind == "new_setting":
        return _apply_setting(book, _setting_value(item, value if manual else None))
    if kind == "timeline_candidate":
        return _apply_timeline(book, _timeline_value(item, value, manual))
    if kind == "missing_char":
        return _apply_missing_char(book, item, _missing_char_value(item, value, manual))
    if kind in _INFO_ONLY_KINDS:
        raise ConflictActionUnavailable("该类型没有可安全执行的通用写回")
    char = str(item.get("char") or "")
    field = str(item.get("field") or "")
    if not char or field not in characters.STATUS_FIELDS:
        raise ConflictActionUnavailable("候选缺少明确角色或状态字段，不能自动写回")
    return _apply_status(book, item, char, field, _status_value(field, value))


def _apply_setting(book: str, value: dict[str, str]) -> str:
    sections = bible.read(book)
    terms = sections.get("术语表", "")
    pattern = re.compile(rf"(?m)^\s*\|\s*{re.escape(value['term'])}\s*\|\s*(.*?)\s*\|\s*$")
    found = pattern.search(terms)
    if found:
        if found.group(1).strip() != value["description"]:
            raise ConflictChanged(f"术语「{value['term']}」已有不同含义，请查看差异后重新判断")
        return f"术语「{value['term']}」已是预期内容，仅补记裁决"
    lines = [line.strip() for line in terms.splitlines() if line.strip()]
    has_table = any(line.startswith("|") for line in lines)
    has_header = any(line.startswith("|") and "术语" in line for line in lines)
    if has_table and not has_header:
        raise ConflictChanged("「术语表」已有表格但表头不含「术语」，请先修正档案表头再裁决")
    row = f"| {value['term']} | {value['description']} |"
    if has_header:
        sections["术语表"] = (terms.rstrip() + "\n" + row).strip()
    else:
        body = terms.rstrip() if lines and lines[-1] != "（待生成）" else ""
        table = "| 术语 | 含义 |\n|---|---|"
        sections["术语表"] = ((body + "\n\n") if body else "") + table + "\n" + row
    bible.write(book, sections)
    verified = bible.read(book).get("术语表", "")
    hit = pattern.search(verified)
    if not hit or hit.group(1).strip() != value["description"]:
        raise ConflictWriteError("术语写入后核对未达预期，冲突仍待处理")
    return f"已补录术语「{value['term']}」"


def _apply_timeline(book: str, value: dict[str, Any]) -> str:
    from . import timeline

    data = timeline.read(book)
    expected = {"章": str(value["chapter_no"]), "事件": value["event"],
                "涉及角色": "、".join(value["characters"]),
                "读者已知": "是" if value["reader_known"] else "否",
                "知情角色": "、".join(value["characters"])}
    for row in data["truth"]:
        if all(row.get(key) == val for key, val in expected.items()):
            return f"时间线 {row['编号']} 已是预期内容，仅补记裁决"
    row = timeline.append_truth(book, value["event"], value["characters"],
                                value["chapter_no"], value["reader_known"])
    verified = next((r for r in timeline.read(book)["truth"] if r.get("编号") == row["编号"]), None)
    if not verified or any(verified.get(key) != val for key, val in expected.items()):
        raise ConflictWriteError("时间线写入后核对未达预期，冲突仍待处理")
    return f"已写入时间线 {row['编号']}"


def _apply_missing_char(book: str, item: dict[str, Any], value: dict[str, Any]) -> str:
    name = value["name"]
    if name in characters.list_characters(book):
        current = characters.read(book, name)
        status = current.get("status") or {}
        if current.get("role") == value["role"] and all(
                status.get(key) == val for key, val in value["status"].items()):
            return f"角色 {name} 已存在且状态符合预期，仅补记裁决"
        raise ConflictChanged(f"角色 {name} 已存在，不能覆盖既有角色；请刷新后重新判断")
    status = dict(value["status"])
    status["updated_chapter"] = item.get("chapter_no", status.get("updated_chapter", 0))
    characters.create(book, name, role=value["role"], status=status)
    verified = characters.read(book, name)
    if any((verified.get("status") or {}).get(key) != val for key, val in status.items()):
        raise ConflictWriteError("角色状态写入后核对未达预期，冲突仍待处理")
    return f"已建档角色 {name} 并应用状态增量"


def _apply_status(book: str, item: dict[str, Any], char: str, field: str, value: Any) -> str:
    data = characters.read(book, char)
    status = dict(data.get("status") or {})
    current = status.get(field)
    if current == value:
        return f"{char}.{field} 已是预期值，仅补记裁决"
    if "old" in item and current != item.get("old"):
        raise ConflictChanged(f"{char}.{field} 已不同于冲突原值，请刷新后重新判断")
    status[field] = value
    status["updated_chapter"] = item.get("chapter_no", status.get("updated_chapter", 0))
    fm = {k: v for k, v in data.items() if not k.startswith("_")}
    fm["status"] = status
    characters.write(book, fm, data["_body"])
    verified = characters.read(book, char).get("status") or {}
    if verified.get(field) != value:
        raise ConflictWriteError("角色状态写入后核对未达预期，冲突仍待处理")
    return f"已写回 {char}.{field} = {value!r}"


def guard(book: str) -> None:
    """台账冲突队列非空 → 禁止开始下一章细纲生成（规格01 §4）。"""
    open_items = list_open(book)
    if open_items:
        raise RuntimeError(
            f"裁决队列非空（{len(open_items)} 条待处理），禁止开新章细纲；请先在裁决页清空"
        )
