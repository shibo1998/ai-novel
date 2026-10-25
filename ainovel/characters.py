"""characters/ 目录：逐角色文件（规格01 §2.3），动态状态由记忆卡回写。

回写规则（规格02 P-V4）：无冲突自动写入；与现档案矛盾（境界倒退、死者复现等）
进人工裁决队列，禁止静默覆盖。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import atomic, bible, books, frontmatter as FM
from .mdtable import parse_table

STATUS_FIELDS = ["realm", "location", "injuries", "inventory", "emotion", "alive", "known_info", "updated_chapter"]

CHAR_SCHEMA: FM.Schema = {
    "required": ["name", "role", "status"],
    "types": {"name": str, "role": str, "status": dict},
    "enums": {"role": ["主角", "女主", "反派", "重要配角", "龙套"]},
}


def chars_dir(book: str) -> Path:
    return books.book_dir(book) / "characters"


def safe_name(name: str) -> str:
    """角色名 → 文件名（去空白与 Windows 非法字符）。"""
    return re.sub(r'[\\/:*?"<>|\s]+', "", name).strip()


def file_for(book: str, name: str) -> Path:
    return chars_dir(book) / f"char-{safe_name(name)}.md"


def list_characters(book: str) -> list[str]:
    d = chars_dir(book)
    if not d.exists():
        return []
    return sorted(p.stem.replace("char-", "", 1) for p in d.glob("char-*.md"))


def read(book: str, name: str) -> dict[str, Any]:
    fm, body = FM.validate_file(file_for(book, name), CHAR_SCHEMA)
    return fm | {"_body": body, "_file": str(file_for(book, name))}


def read_all(book: str) -> dict[str, dict[str, Any]]:
    return {name: read(book, name) for name in list_characters(book)}


def write(book: str, fm: dict[str, Any], body: str) -> None:
    FM.validate(fm, CHAR_SCHEMA)
    atomic.write_text(file_for(book, fm["name"]), FM.dump(fm, body))


def create(book: str, name: str, role: str = "龙套", sections: dict[str, str] | None = None) -> None:
    if file_for(book, name).exists():
        raise FileExistsError(f"角色已存在: {name}")
    fm = {
        "name": name,
        "role": role,
        "status": {
            "realm": "", "location": "", "injuries": [], "inventory": [],
            "emotion": "", "alive": True, "known_info": [], "updated_chapter": 0,
        },
    }
    secs = sections or {}
    body = "\n".join(
        f"## {s}\n\n{secs.get(s, '（待填）')}\n"
        for s in ["表层信息", "声音特征", "核心三要素", "内在矛盾", "过往创伤",
                  "标志行为", "关系网络", "行为底线", "弧光预设", "对话试金石"]
    )
    write(book, fm, body)


# ---------------------------------------------------------------------------
# 记忆卡回写（P-V4 diff 到 frontmatter.status）
# ---------------------------------------------------------------------------

class Conflict:
    """台账/状态冲突：进裁决队列（规格01 §4：队列非空禁止开下一章细纲）。"""

    def __init__(self, kind: str, book: str, chapter_no: int, detail: str,
                 old: Any, new: Any, char: str = "", field: str = ""):
        self.kind = kind
        self.book = book
        self.chapter_no = chapter_no
        self.char = char
        self.field = field
        self.detail = detail
        self.old = old
        self.new = new

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Conflict":
        return Conflict(d["kind"], d["book"], d["chapter_no"], d["detail"], d["old"], d["new"],
                        d.get("char", ""), d.get("field", ""))


def apply_status_updates(
    book: str, chapter_no: int, char_updates: list[dict[str, Any]],
    *, realm_order: list[str] | None = None,
) -> tuple[list[str], list[Conflict]]:
    """把 V4 的 char_updates diff 到角色卡 frontmatter。

    返回 (自动写入的角色名列表, 冲突列表)。冲突不写盘，由裁决队列处理。
    """
    applied: list[str] = []
    conflicts: list[Conflict] = []
    if realm_order is None:
        try:
            realm_order = bible.realm_order(book)
        except Exception:
            realm_order = []
    for upd in char_updates:
        name = upd.get("name", "")
        changes = upd.get("changes") or {}
        if not name or not changes:
            continue
        if name not in list_characters(book):
            conflicts.append(Conflict("missing_char", book, chapter_no,
                                      f"角色 {name} 不存在于 characters/，需人工建档", None, changes, char=name))
            continue
        data = read(book, name)
        status: dict[str, Any] = dict(data.get("status") or {})
        block: list[Conflict] = []
        for field, new_val in changes.items():
            old_val = status.get(field)
            if field == "alive" and old_val is False and new_val is True:
                block.append(Conflict("dead_reappear", book, chapter_no,
                                      f"角色 {name} 已标记死亡，记忆卡试图复活（alive→True）",
                                      old_val, new_val, char=name, field=field))
                continue
            if field == "realm" and old_val and new_val and realm_order:
                oi = _realm_index(old_val, realm_order)
                ni = _realm_index(new_val, realm_order)
                if oi is not None and ni is not None and ni < oi:
                    block.append(Conflict("realm_regress", book, chapter_no,
                                          f"角色 {name} 境界倒退: {old_val} → {new_val}",
                                          old_val, new_val, char=name, field=field))
                    continue
            if field not in STATUS_FIELDS:
                # 未知动态字段：允许新增，但记为提醒级冲突供人审
                block.append(Conflict("unknown_field", book, chapter_no,
                                      f"角色 {name} 出现未定义状态字段 {field}",
                                      old_val, new_val, char=name, field=field))
                continue
            status[field] = new_val
        if block:
            # 有阻断冲突则本角色整卡不写（含已通过字段，避免半新半旧的不一致状态）
            conflicts.extend(block)
            continue
        status["updated_chapter"] = chapter_no
        fm = {k: v for k, v in data.items() if not k.startswith("_")}
        fm["status"] = status
        write(book, fm, data["_body"])
        applied.append(name)
    return applied, conflicts


def _realm_index(val: str, order: list[str]) -> int | None:
    for i, r in enumerate(order):
        if r in val or val in r:
            return i
    return None


def relationships(book: str, name: str) -> list[dict[str, str]]:
    """角色卡"关系网络"节表格：对象|关系|不稳定因素。"""
    data = read(book, name)
    m = re.search(r"(?ms)^##\s*关系网络\s*\n(.*?)(?=^##\s|\Z)", data["_body"])
    if not m:
        return []
    _, rows = parse_table(m.group(1), "对象")
    return rows
