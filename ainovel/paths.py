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


def book_dir(book_name: str) -> Path:
    return books_dir() / book_name
