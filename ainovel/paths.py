"""路径解析：项目根 / prompts / lint / books / data。

根目录解析顺序：
1. 环境变量 AINOVEL_HOME
2. 打包 exe（PyInstaller frozen）时取 exe 所在目录
3. 否则取本包的父目录（仓库根）
"""
from __future__ import annotations

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
    validate_book_name(book_name)
    return books_dir() / book_name
