"""YAML frontmatter 解析 / 序列化 / schema 校验（规格01 §5.2：校验失败拒绝写回，不静默降级）。"""
from __future__ import annotations

import re
from typing import Any, Callable

import yaml

_FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


class FrontmatterError(Exception):
    """frontmatter 缺失 / 语法错误 / schema 校验失败。"""


def parse(text: str) -> tuple[dict[str, Any], str]:
    """返回 (frontmatter, body)。无 frontmatter 时抛 FrontmatterError。"""
    m = _FM_RE.match(text)
    if not m:
        raise FrontmatterError("缺少 YAML frontmatter（--- 包裹段）")
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        raise FrontmatterError(f"frontmatter YAML 解析失败: {e}") from e
    if fm is None:
        fm = {}
    if not isinstance(fm, dict):
        raise FrontmatterError("frontmatter 必须是键值映射")
    body = text[m.end():]
    return fm, body


def dump(fm: dict[str, Any], body: str) -> str:
    """稳定序列化：保持插入键序，中文原样输出。"""
    if not isinstance(fm, dict):
        raise FrontmatterError("frontmatter 必须是键值映射")
    y = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, default_flow_style=False, width=10_000)
    return f"---\n{y}---\n{body}"


# ---------------------------------------------------------------------------
# 轻量 schema 校验：字段类型 / 必填 / 枚举。够用即可，不引 jsonschema。
# ---------------------------------------------------------------------------

def _type_name(t: type | tuple) -> str:
    if isinstance(t, tuple):
        return "|".join(x.__name__ for x in t)
    return getattr(t, "__name__", str(t))


def validate(fm: dict[str, Any], schema: dict[str, Any]) -> None:
    """schema 形如：
    {
        "required": ["chapter_no", ...],
        "types": {"chapter_no": int, "word_range": (list, type(None))},
        "enums": {"status": ["draft", "final"]},
    }
    任一键可缺省。校验失败抛 FrontmatterError。
    """
    errors: list[str] = []
    for key in schema.get("required", []):
        # required = 键必须存在且非 None；空串是否合法由 non_empty 单独约束
        if key not in fm or fm[key] is None:
            errors.append(f"缺少必填字段: {key}")
    for key in schema.get("non_empty", []):
        if key in fm and (fm[key] is None or (isinstance(fm[key], str) and not fm[key].strip())):
            errors.append(f"字段 {key} 不能为空")
    for key, allowed in schema.get("types", {}).items():
        if key not in fm or fm[key] is None:
            continue
        allowed_tuple = allowed if isinstance(allowed, tuple) else (allowed,)
        # bool 是 int 子类：类型不含 bool 时显式拒绝
        if isinstance(fm[key], bool) and bool not in allowed_tuple:
            errors.append(f"字段 {key} 类型应为 {_type_name(allowed)}，实际 bool")
            continue
        if not isinstance(fm[key], allowed):
            errors.append(f"字段 {key} 类型应为 {_type_name(allowed)}，实际 {type(fm[key]).__name__}")
    for key, allowed_values in schema.get("enums", {}).items():
        if key not in fm or fm[key] is None:
            continue
        if fm[key] not in allowed_values:
            errors.append(f"字段 {key} 取值 {fm[key]!r} 不在允许集合 {allowed_values}")
    if errors:
        raise FrontmatterError("; ".join(errors))


def read_file(path) -> tuple[dict[str, Any], str]:
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        raise FrontmatterError(f"文件不存在: {p}")
    return parse(p.read_text(encoding="utf-8"))


def validate_file(path, schema: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """读文件 + schema 校验，一步完成。"""
    fm, body = read_file(path)
    validate(fm, schema)
    return fm, body


Schema = dict[str, Any]
Validator = Callable[[dict[str, Any]], None]
