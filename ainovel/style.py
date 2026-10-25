"""M5 文风系统：语料 → F1 特征卡 → F2 注入（规格02 F1/F2）。

存储：books/<书名>/style/语料.txt 与 特征卡.md；config.style_enabled 控制注入。
"""
from __future__ import annotations

from . import atomic, books
from .paths import book_dir


def style_dir(book: str):
    return book_dir(book) / "style"


def corpus_path(book: str):
    return style_dir(book) / "语料.txt"


def card_path(book: str):
    return style_dir(book) / "特征卡.md"


def read_corpus(book: str) -> str:
    p = corpus_path(book)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def write_corpus(book: str, text: str) -> None:
    style_dir(book).mkdir(parents=True, exist_ok=True)
    atomic.write_text(corpus_path(book), text)


def read_card(book: str) -> str:
    p = card_path(book)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def write_card(book: str, text: str) -> None:
    style_dir(book).mkdir(parents=True, exist_ok=True)
    atomic.write_text(card_path(book), text)


def enabled(book: str) -> bool:
    if not read_card(book):
        return False
    return bool(books.load_config(book).get("style_enabled", False))


def set_enabled(book: str, on: bool) -> None:
    cfg = books.load_config(book)
    cfg["style_enabled"] = bool(on)
    books.save_config(book, cfg)
