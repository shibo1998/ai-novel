"""（规格09 §2）透明文风计量：只统计、不评分、不触发改稿。"""
from __future__ import annotations

import bisect
import math
import re
import statistics
from pathlib import Path
from typing import Any

from .paths import lint_dir

MAX_POSITIONS = 20


def _words(path: Path) -> list[str]:
    """（规格09 §5）读取字面词表；缺失、空表或非法内容不能冒充零计数。"""
    text = path.read_text(encoding="utf-8-sig")
    words = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not words:
        raise ValueError(f"{path.name} 为空或只有注释")
    for word in words:
        if len(word) > 64 or any(ch.isspace() or not ch.isprintable() or ch == "\ufffd" for ch in word):
            raise ValueError(f"{path.name} 含非法词条（仅接受1—64个无空白可见字符）")
    return sorted(set(words), key=lambda word: (-len(word), word))


def _pattern(words: list[str]) -> re.Pattern[str]:
    return re.compile("|".join(re.escape(word) for word in words))


def _paragraphs(body: str) -> dict[str, Any]:
    """（规格09 §2.2）空行分段，段内换行不拆段；分位数使用最近秩。"""
    normalized = body.replace("\r\n", "\n").replace("\r", "\n")
    lengths = [len(re.sub(r"\s+", "", part)) for part in re.split(r"\n[^\S\n]*\n", normalized) if part.strip()]
    if not lengths:
        return {"count": 0, "mean_chars": None, "median_chars": None, "p90_chars": None, "short_ratio": None}
    ordered = sorted(lengths)
    count = len(lengths)
    return {"count": count, "mean_chars": round(sum(lengths) / count, 2),
            "median_chars": round(statistics.median(lengths), 2),
            "p90_chars": ordered[math.ceil(0.9 * count) - 1],
            "short_ratio": round(sum(length <= 10 for length in lengths) / count, 4)}


def _simile_cues(body: str) -> dict[str, Any]:
    """（规格09 §2.2）全文含对白的词面线索；位置有界但计数不截断。"""
    result: dict[str, Any] = {"status": "unavailable", "scope": "all_text", "count": None,
                              "per_1000_chars": None, "positions": []}
    try:
        cues = _pattern(_words(lint_dir() / "simile_cues.txt"))
        exclusions = _pattern(_words(lint_dir() / "simile_exclusions.txt"))
    except (OSError, UnicodeError, ValueError) as exc:
        # 配置错误留在计量结果，不进入旧机检ineffective或模型摘要。
        detail = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, UnicodeError) else type(exc).__name__
        result["reason"] = f"显式比喻线索词表不可用：{detail}；请检查lint计量词表。"
        return result
    # 前瞻保留重叠排除词，再合并区间；普通finditer会漏掉交叠词尾。
    excluded: list[tuple[int, int]] = []
    for match in re.finditer(f"(?=({exclusions.pattern}))", body):
        start, end = match.start(1), match.end(1)
        if excluded and start <= excluded[-1][1]:
            excluded[-1] = (excluded[-1][0], max(end, excluded[-1][1]))
        else:
            excluded.append((start, end))
    line_starts = [match.end() for match in re.finditer(r"\r\n|\r|\n", body)]
    count = 0
    exclusion_index = 0
    positions: list[dict[str, Any]] = []
    for match in cues.finditer(body):
        while exclusion_index < len(excluded) and excluded[exclusion_index][1] <= match.start():
            exclusion_index += 1
        if exclusion_index < len(excluded) and excluded[exclusion_index][0] < match.end():
            continue
        count += 1
        if len(positions) < MAX_POSITIONS:
            quote = re.sub(r"\s+", " ", body[max(0, match.start() - 10):match.end() + 20]).strip()
            positions.append({"line": bisect.bisect_right(line_starts, match.start()) + 1,
                              "start": match.start(), "end": match.end(), "cue": match.group(), "quote": quote[:40]})
    chars = len(re.sub(r"\s+", "", body))
    result.update(status="measured", count=count, per_1000_chars=round(count * 1000 / chars, 2) if chars else None,
                  positions=positions)
    return result


def analyze(body: str) -> dict[str, Any]:
    """（规格09 B-T01—03）同文本与词表结果可重复，独立于D-14保护区和评分。"""
    return {"version": 1, "paragraphs": _paragraphs(body), "simile_cues": _simile_cues(body)}
