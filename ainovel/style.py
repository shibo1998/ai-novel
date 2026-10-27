"""（规格06 §4）人工文风资产、并发指纹与确定性完整范例。"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import atomic, books, operations
from .paths import book_dir

SCENE_TYPES = {"opening", "dialogue", "action", "reasoning", "transition",
               "breakthrough", "climax", "general"}
TASK_TYPES = {"W1": "general", "W2": "dialogue", "W3": "opening",
              "W4": "breakthrough", "W5": "climax"}


class StyleConflict(ValueError):
    """（规格06 §10.1）资产已变化，调用方应映射为409。"""


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def style_dir(book: str) -> Path:
    return book_dir(book) / "style"


def corpus_path(book: str) -> Path:
    return style_dir(book) / "语料.txt"


def card_path(book: str) -> Path:
    return style_dir(book) / "特征卡.md"


def examples_path(book: str) -> Path:
    return style_dir(book) / "范例.json"


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    with path.open(encoding="utf-8", newline="") as stream:
        return stream.read()


def read_corpus(book: str) -> str:
    return _read(corpus_path(book))


def read_card(book: str) -> str:
    return _read(card_path(book))


def _write(path: Path, text: str, expected_fingerprint: str | None) -> str:
    if not isinstance(text, str):
        raise ValueError("文风资料必须是文本")
    if expected_fingerprint is not None and fingerprint(_read(path)) != expected_fingerprint:
        raise StyleConflict("文风资料已变化，请刷新后重试")
    atomic.write_text(path, text)
    return fingerprint(text)


@operations.serialized
def write_corpus(book: str, text: str, expected_fingerprint: str | None = None) -> str:
    return _write(corpus_path(book), text, expected_fingerprint)


@operations.serialized
def write_card(book: str, text: str, expected_fingerprint: str | None = None) -> str:
    return _write(card_path(book), text, expected_fingerprint)


def enabled(book: str) -> bool:
    """（规格06 §4.1）开关关闭不读取资产；启用仍要求正式卡存在。"""
    if not books.load_config(book).get("style_enabled", False):
        return False
    return bool(read_card(book).strip())


@operations.serialized
def set_enabled(book: str, on: bool) -> None:
    cfg = books.load_config(book)
    cfg["style_enabled"] = bool(on)
    books.save_config(book, cfg)


def _paragraphs(corpus: str) -> list[dict[str, Any]]:
    """（规格06 §4.3）按空行分段，保留段内原文与服务端范围。"""
    result: list[dict[str, Any]] = []
    start = 0
    separators = list(re.finditer(r"(?>\r\n|\n|\r)[ \t]*(?>\r\n|\n|\r)(?:[ \t]*(?>\r\n|\n|\r))*", corpus))
    for end, next_start in [(m.start(), m.end()) for m in separators] + [(len(corpus), len(corpus))]:
        chunk = corpus[start:end]
        if chunk.strip():
            left = len(chunk) - len(chunk.lstrip("\r\n"))
            right = len(chunk.rstrip("\r\n"))
            no = len(result) + 1
            result.append({"id": f"p{no:04d}", "no": no, "text": chunk[left:right],
                           "_start": start + left, "_end": start + right})
        start = next_start
    return result


def _range_text(corpus: str, paragraphs: list[dict[str, Any]], start: int, end: int) -> str:
    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(paragraphs):
        raise ValueError("范例段落范围不存在或不完整")
    return corpus[paragraphs[start - 1]["_start"]:paragraphs[end - 1]["_end"]]


def _index(book: str, corpus: str, paragraphs: list[dict[str, Any]]) -> tuple[dict[str, Any], bool, list[str]]:
    empty = {"schema_version": 1, "corpus_fingerprint": fingerprint(corpus), "examples": []}
    path = examples_path(book)
    if not path.exists():
        return empty, False, ["legacy：没有人工范例索引，使用完整段落兼容样例"]
    try:
        data = json.loads(_read(path))
        if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("examples"), list):
            raise ValueError("范例索引格式非法")
        if not re.fullmatch(r"[0-9a-f]{64}", str(data.get("corpus_fingerprint", ""))):
            raise ValueError("范例索引语料指纹非法")
        stale = data["corpus_fingerprint"] != fingerprint(corpus)
        ids: set[str] = set()
        for item in data["examples"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or item["id"] in ids:
                raise ValueError("范例ID非法或重复")
            ids.add(item["id"])
            tags = item.get("scene_types")
            if not isinstance(tags, list) or not tags or any(not isinstance(t, str) or t not in SCENE_TYPES for t in tags):
                raise ValueError("范例场景标签非法")
            if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("content_fingerprint", ""))):
                raise ValueError("范例内容指纹非法")
            start, end = item.get("start_paragraph"), item.get("end_paragraph")
            if type(start) is not int or type(end) is not int or not 1 <= start <= end:
                raise ValueError("范例段落范围非法")
            if not stale:
                text = _range_text(corpus, paragraphs, item.get("start_paragraph"), item.get("end_paragraph"))
                if fingerprint(text) != item["content_fingerprint"]:
                    raise ValueError("范例内容指纹不匹配")
        return data, stale, ["范例索引已过期，未套用到新语料"] if stale else []
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return empty, True, [f"范例索引不可用：{exc}"]


def describe(book: str) -> dict[str, Any]:
    """（规格06 §10.1）编辑器资料；只读，不迁移或创建兼容索引。"""
    corpus, card = read_corpus(book), read_card(book)
    paragraphs = _paragraphs(corpus)
    index, stale, warnings = _index(book, corpus, paragraphs)
    return {"corpus": corpus, "card": card, "enabled": enabled(book),
            "corpus_fingerprint": fingerprint(corpus), "card_fingerprint": fingerprint(card),
            "examples": index, "examples_stale": stale,
            "paragraphs": [{k: p[k] for k in ("id", "no", "text")} for p in paragraphs],
            "warnings": warnings}


@operations.serialized
def save_examples(book: str, corpus_fingerprint: str, examples: list[dict[str, Any]]) -> dict[str, Any]:
    """（规格06 §4.3）服务端生成稳定ID，原子发布范围索引。"""
    corpus = read_corpus(book)
    if fingerprint(corpus) != corpus_fingerprint:
        raise StyleConflict("语料已变化，请重新选择范例")
    if not isinstance(examples, list):
        raise ValueError("范例必须是数组")
    paragraphs = _paragraphs(corpus)
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in examples:
        if not isinstance(item, dict):
            raise ValueError("范例必须是对象")
        start, end = item.get("start_paragraph"), item.get("end_paragraph")
        text = _range_text(corpus, paragraphs, start, end)
        tags = item.get("scene_types", ["general"])
        if not isinstance(tags, list) or not tags or any(not isinstance(t, str) or t not in SCENE_TYPES for t in tags):
            raise ValueError("范例场景标签非法")
        tags = sorted(set(tags))
        fp = fingerprint(text)
        eid = "e" + fingerprint(json.dumps([start, end, tags, fp], ensure_ascii=False))[:24]
        if eid in seen:
            raise ValueError("范例范围和标签重复")
        seen.add(eid)
        entries.append({"id": eid, "start_paragraph": start, "end_paragraph": end,
                        "scene_types": tags, "content_fingerprint": fp})
    index = {"schema_version": 1, "corpus_fingerprint": corpus_fingerprint,
             "examples": sorted(entries, key=lambda e: e["id"])}
    atomic.write_text(examples_path(book), json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    return index


def context(book: str, task_type: str = "general") -> dict[str, Any]:
    """（规格06 §4.4）最多两个完整范例/1200可见字，关闭时不读取任何文风资产。"""
    result: dict[str, Any] = {"enabled": enabled(book), "card": "", "card_fingerprint": "",
                              "corpus_fingerprint": "", "examples": [], "warnings": []}
    if not result["enabled"]:
        return result
    corpus, card = read_corpus(book), read_card(book)
    paragraphs = _paragraphs(corpus)
    index, stale, warnings = _index(book, corpus, paragraphs)
    result.update(card=card, card_fingerprint=fingerprint(card), corpus_fingerprint=fingerprint(corpus), warnings=warnings)
    if not card.strip():
        warnings.append("已启用文风但正式特征卡为空")
    if stale:
        return result
    legacy = not examples_path(book).exists()
    candidates = ([{"id": p["id"], "start_paragraph": p["no"], "end_paragraph": p["no"],
                    "scene_types": ["general"], "content_fingerprint": fingerprint(p["text"])} for p in paragraphs]
                  if legacy else index["examples"])
    task = TASK_TYPES.get(task_type, task_type)
    candidates = sorted((e for e in candidates if task in e["scene_types"] or "general" in e["scene_types"]),
                        key=lambda e: (0 if task in e["scene_types"] else 1, e["id"]))
    used = 0
    for item in candidates:
        text = _range_text(corpus, paragraphs, item["start_paragraph"], item["end_paragraph"])
        chars = len(re.sub(r"\s+", "", text))
        if used + chars > 1200:
            warnings.append(f"范例 {item['id']} 超出输入样例预算，整段舍弃")
            continue
        result["examples"].append({"id": item["id"], "text": text,
                                   "content_fingerprint": item["content_fingerprint"], "legacy": legacy})
        used += chars
        if len(result["examples"]) == 2:
            break
    if not result["examples"]:
        warnings.append("没有适用且可完整容纳的文风范例")
    return result
