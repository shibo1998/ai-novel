"""全书摘要状态：最近章摘要环形缓冲（保留最近 2 章）+ 累计摘要（规格03 §2.7）。

存 data/summaries/<书名>.json。累计摘要超长时压缩保留头部+尾部。
"""
from __future__ import annotations

import json
from typing import Any

from . import atomic
from .paths import data_dir

RECENT_KEEP = 2
CUMULATIVE_MAX = 4000


def _path(book: str):
    return data_dir() / "summaries" / f"{book}.json"


def load(book: str) -> dict[str, Any]:
    p = _path(book)
    if not p.exists():
        return {"recent": [], "cumulative": ""}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"recent": [], "cumulative": ""}


def push(book: str, chapter_no: int, summary: str) -> dict[str, Any]:
    data = load(book)
    data["recent"] = [r for r in data["recent"] if r["no"] != chapter_no]
    data["recent"].append({"no": chapter_no, "summary": summary})
    data["recent"] = data["recent"][-RECENT_KEEP:]
    line = f"第{chapter_no}章：{summary}".strip()
    cum = (data.get("cumulative", "") + ("\n" if data.get("cumulative") else "") + line)
    if len(cum) > CUMULATIVE_MAX:
        head = cum[: CUMULATIVE_MAX // 2]
        tail = cum[-CUMULATIVE_MAX // 2:]
        cum = head + "\n（……早期章节摘要已压缩……）\n" + tail
    data["cumulative"] = cum
    p = _path(book)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(p, json.dumps(data, ensure_ascii=False, indent=2))
    return data
