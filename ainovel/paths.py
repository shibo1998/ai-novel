"""路径解析：项目根 / prompts / lint / books / data。

根目录解析顺序：
1. 环境变量 AINOVEL_HOME
2. 打包 exe（PyInstaller frozen）时取 exe 所在目录
3. 否则取本包的父目录（仓库根）
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path


def project_root() -> Path:
    env = os.environ.get("AINOVEL_HOME")
    if env:
        return Path(env).resolve()
    if getattr(sys, "frozen", False):  # PyInstaller
        return Path(sys.executable).parent.resolve()
    return Path(__file__).resolve().parent.parent


def prompts_dir() -> Path:
    return project_root() / "prompts"


def lint_dir() -> Path:
    return project_root() / "lint"


def books_dir() -> Path:
    return project_root() / "books"


def data_dir() -> Path:
    return project_root() / "data"


# 外挂书登记表：书名 → 绝对路径（规格05 F-07）
ATTACHED_REGISTRY = "attached_books.json"


def _registry_path() -> Path:
    return data_dir() / ATTACHED_REGISTRY


def attached_books() -> dict[str, str]:
    """读活动外挂书登记表（规格05 F-07）；文件是可删库恢复的路径真相源。"""
    p = _registry_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("登记表必须是对象")
        registry: dict[str, str] = {}
        for name, raw in data.items():
            if not isinstance(raw, str) or not raw or not Path(raw).is_absolute():
                raise ValueError("外挂书路径必须是绝对路径")
            validate_book_name(name)
            registry[name] = raw
        return registry
    except (ValueError, OSError) as e:
        logging.getLogger(__name__).warning("外挂书登记表损坏，按空表处理: %s", e)
        return {}


def save_attached(registry: dict[str, str]) -> None:
    """原子保存活动外挂路径（规格01 §5.1、规格05 F-07），不改书稿配置。"""
    from . import atomic

    for name, raw in registry.items():
        validate_book_name(name)
        if not isinstance(raw, str) or not raw or not Path(raw).is_absolute():
            raise ValueError("外挂书路径必须是绝对路径")
    atomic.write_text(_registry_path(), json.dumps(registry, ensure_ascii=False, indent=2))


def validate_book_name(name: str) -> str:
    """书名守卫（P0 防路径穿越：book_dir 单点把关，全部 {book} 端点自动受保护）。

    规则：非空、≤100 字符、不含路径分隔符与 Windows 非法字符、不含控制字符、
    不得为 "." / ".."、不得以点或空格结尾（Windows 会静默剥离，导致目录名与 DB 不一致）。
    中文与书名内部空格合法。
    """
    if not name or not name.strip():
        raise ValueError(f"非法书名: {name!r}")
    if len(name) > 100:
        raise ValueError(f"书名过长（>100 字符）: {name[:20]!r}…")
    if any(c in name for c in '\\/:*?"<>|'):
        raise ValueError(f"非法书名（含路径分隔符或 Windows 非法字符）: {name!r}")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError(f"非法书名（含控制字符）: {name!r}")
    if name in (".", ".."):
        raise ValueError(f"非法书名: {name!r}")
    if name[-1] in ". ":
        raise ValueError(f"非法书名（以点/空格结尾）: {name!r}")
    return name


def book_dir(book_name: str) -> Path:
    """书目录解析：外挂书走登记表，内置书走 books/<书名>/。

    单点解析的好处：所有 {book} 端点自动支持外挂书，无需各处判断。
    """
    validate_book_name(book_name)
    attached = attached_books().get(book_name)
    if attached:
        return Path(attached)
    return books_dir() / book_name
