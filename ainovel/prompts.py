"""提示词库：加载 / digest / 书级覆盖 / 变量渲染（规格02 §1）。

- 每条提示词一个文件 prompts/<层>/<ID>.md，frontmatter:
  id / name / stage / model_stage / temperature / version / inputs / output
- 变量统一 〖变量名〗，运行时装配器/程序填充；渲染后仍残留 〖〗 视为错误
- 每次 LLM 调用记录 prompt_digest（SHA-256）入 generation_log
- 书级覆盖：books/<书名>/prompts/<层>/<ID>.md 优先
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import frontmatter as FM
from .paths import book_dir, prompts_dir

LAYERS = ["system", "generate", "write", "review", "style"]

REQUIRED_FM = ["id", "stage", "model_stage", "temperature", "version"]

_VAR_RE = re.compile(r"〖([^〗]+)〗")


class PromptError(Exception):
    pass


@dataclass
class Prompt:
    layer: str
    path: Path
    fm: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    digest: str = ""
    source: str = "global"  # global | book

    @property
    def id(self) -> str:
        return self.fm["id"]

    def full_text(self) -> str:
        return FM.dump(self.fm, self.body)


def _load_file(path: Path, source: str) -> Prompt:
    text = path.read_text(encoding="utf-8")
    fm, body = FM.parse(text)
    missing = [k for k in REQUIRED_FM if k not in fm]
    if missing:
        raise PromptError(f"{path}: frontmatter 缺字段 {missing}")
    return Prompt(layer=path.parent.name, path=path, fm=fm, body=body,
                  digest=hashlib.sha256(text.encode("utf-8")).hexdigest(), source=source)


def library(book: str | None = None) -> dict[str, Prompt]:
    """全量加载；书级同名覆盖全局。键 = 提示词 ID。"""
    index: dict[str, Prompt] = {}
    root = prompts_dir()
    if root.exists():
        for layer in LAYERS:
            d = root / layer
            if not d.exists():
                continue
            for f in sorted(d.glob("*.md")):
                p = _load_file(f, "global")
                index[p.id] = p
    if book:
        broot = book_dir(book) / "prompts"
        if broot.exists():
            for layer in LAYERS:
                d = broot / layer
                if not d.exists():
                    continue
                for f in sorted(d.glob("*.md")):
                    p = _load_file(f, "book")
                    index[p.id] = p
    return index


def get(prompt_id: str, book: str | None = None) -> Prompt:
    lib = library(book)
    p = lib.get(prompt_id)
    if p is None:
        raise PromptError(f"提示词不存在: {prompt_id}")
    return p


def render(prompt_id: str, variables: dict[str, Any], book: str | None = None) -> tuple[Prompt, str]:
    """填充 〖变量〗。渲染后仍有残留变量 → PromptError（不静默发给模型）。"""
    p = get(prompt_id, book)
    text = p.full_text()
    missing: set[str] = set()

    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name not in variables:
            missing.add(name)
            return m.group(0)
        return str(variables[name])

    rendered = _VAR_RE.sub(repl, text)
    if missing:
        raise PromptError(f"{prompt_id}: 未提供变量 {sorted(missing)}")
    return p, rendered


def list_meta(book: str | None = None) -> list[dict[str, Any]]:
    return [
        {"id": p.id, "name": p.fm.get("name", ""), "layer": p.layer, "stage": p.fm["stage"],
         "model_stage": p.fm["model_stage"], "temperature": p.fm["temperature"],
         "version": p.fm["version"], "digest": p.digest[:12], "source": p.source}
        for p in sorted(library(book).values(), key=lambda x: (x.layer, x.id))
    ]
