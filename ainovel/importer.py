"""导入现存 txt（规格05 F-08）：把裸 txt 变成可继续写作的工程。

两条铁律：
1. **两段式**：预演阶段一个字节都不写。切章规则错了会毁掉整份稿。
2. **断点续跑**：300 章中途崩溃不能从头再来——每章写完立即更新 job 文件。

不写细纲（历史章本来就没有），只在 OUTLINE 标"（导入，无细纲）"，
绝不伪造一份看不出来的假细纲。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from . import atomic, chapters, db, outline, statemachine
from .paths import data_dir

logger = logging.getLogger(__name__)

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "utf-16")

CHAPTER_PATTERNS = [
    r"^第[0-9零一二三四五六七八九十百千]+[章节回].*$",
    r"^[0-9]{1,4}[、.]\s*\S+",
    r"^Chapter\s+\d+.*$",
    r"^【第.+?章】.*$",
]

MIN_CHAPTER_CHARS = 500  # 低于此值提示 short（不阻断）

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}


class ImportError_(Exception):
    pass


# ---------------------------------------------------------------------------
# 编码与切章
# ---------------------------------------------------------------------------

def detect_encoding(raw: bytes) -> str | None:
    """按 utf-8-sig → utf-8 → gb18030 → utf-16 依次尝试；全失败返回 None（不猜）。"""
    for enc in ENCODINGS:
        try:
            raw.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def read_text(path: str | Path, encoding: str | None = None) -> tuple[str, str]:
    p = Path(path)
    if not p.exists():
        raise ImportError_(f"文件不存在: {p}")
    raw = p.read_bytes()
    enc = encoding or detect_encoding(raw)
    if not enc:
        raise ImportError_(f"编码无法识别（试过 {'/'.join(ENCODINGS)}），请手工指定：{p}")
    try:
        return raw.decode(enc), enc
    except UnicodeDecodeError as e:
        raise ImportError_(f"用 {enc} 解码失败: {e}") from e


def cn2num(text: str) -> int | None:
    """章节号解析：'12' / '十二' / '一百零三' → int；解析不了返回 None。"""
    s = (text or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    s = s.replace("两", "二")
    if "百" in s:
        head, _, tail = s.partition("百")
        try:
            h = _CN_DIGITS.get(head, 1) if head else 1
            t = cn2num(tail.replace("零", "")) if tail else 0
            return h * 100 + (t or 0)
        except Exception:
            return None
    if "十" in s:
        head, _, tail = s.partition("十")
        h = _CN_DIGITS.get(head, 1) if head else 1
        t = _CN_DIGITS.get(tail, 0) if tail else 0
        return h * 10 + t
    if len(s) == 1 and s in _CN_DIGITS:
        return _CN_DIGITS[s]
    return None


def guess_pattern(text: str) -> str:
    """挑命中章标题最多的正则（默认值，最终由用户在预演页确认）。"""
    best, best_n = CHAPTER_PATTERNS[0], 0
    for pat in CHAPTER_PATTERNS:
        n = len(re.findall(pat, text, flags=re.M))
        if n > best_n:
            best, best_n = pat, n
    return best


def split_chapters(text: str, pattern: str) -> list[dict[str, Any]]:
    """按正切章。没有命中任何标题时整篇算一章（并在 warnings 里说明）。"""
    lines = text.splitlines()
    rx = re.compile(pattern)
    marks: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        if rx.match(line.strip()):
            marks.append((idx, line.strip()))

    if not marks:
        body = text.strip()
        return [{"no": 1, "title": "（未识别到章标题）", "chars": _chars(body),
                 "start_line": 1, "body": body, "parsed_no": None}]

    out: list[dict[str, Any]] = []
    for i, (idx, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(lines)
        body = "\n".join(lines[idx + 1:end]).strip()
        parsed = _parse_no(title)
        out.append({"no": i + 1, "title": title[:60], "chars": _chars(body),
                    "start_line": idx + 1, "body": body, "parsed_no": parsed})
    return out


def _chars(s: str) -> int:
    return len(re.sub(r"\s+", "", s))


def _parse_no(title: str) -> int | None:
    m = re.search(r"第\s*([0-9零一二三四五六七八九十百千两]+)\s*[章节回]", title)
    if m:
        return cn2num(m.group(1))
    m = re.match(r"^([0-9]{1,4})[、.]", title)
    if m:
        return int(m.group(1))
    m = re.match(r"^Chapter\s+(\d+)", title, flags=re.I)
    if m:
        return int(m.group(1))
    return None


def _warnings(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    warns: list[dict[str, str]] = []
    nums = [it["parsed_no"] for it in items if it.get("parsed_no")]
    if nums:
        if nums[0] > 1:
            warns.append({"kind": "gap", "detail": f"缺第 1-{nums[0] - 1} 章"})
        have = set(nums)
        missing = [n for n in range(min(nums), max(nums) + 1) if n not in have]
        if missing:
            preview = "、".join(str(n) for n in missing[:10])
            warns.append({"kind": "gap", "detail": f"缺第 {preview} 章"})
        if len(set(nums)) != len(nums):
            dup = sorted({n for n in nums if nums.count(n) > 1})
            warns.append({"kind": "duplicate_no",
                          "detail": f"章号重复：{'、'.join(map(str, dup))}"})
    titles = [it["title"] for it in items]
    dups = sorted({t for t in titles if titles.count(t) > 1})
    if dups:
        warns.append({"kind": "duplicate_title", "detail": f"标题相同：{'、'.join(dups[:5])}"})
    shorts = [it for it in items if it["chars"] < MIN_CHAPTER_CHARS]
    if shorts:
        preview = "、".join(f"第{it['no']}章({it['chars']}字)" for it in shorts[:5])
        warns.append({"kind": "short", "detail": f"正文过短：{preview}"})
    if len(items) >= 1 and items[0]["title"] == "（未识别到章标题）":
        warns.append({"kind": "no_heading",
                      "detail": "没有识别到任何章标题，整篇将作为一章导入"})
    return warns


# ---------------------------------------------------------------------------
# 预演（不写盘）
# ---------------------------------------------------------------------------

def preview(file_path: str | Path, regex: str | None = None) -> dict[str, Any]:
    text, enc = read_text(file_path)
    pattern = regex or guess_pattern(text)
    try:
        re.compile(pattern)
    except re.error as e:
        raise ImportError_(f"切章正则非法: {pattern!r}（{e}）") from e
    items = split_chapters(text, pattern)
    return {
        "encoding": enc,
        "total_chars": _chars(text),
        "chapters": [{k: it[k] for k in ("no", "title", "chars", "start_line")} for it in items],
        "warnings": _warnings(items),
        "guessed_regex": pattern,
        "file_path": str(Path(file_path)),
    }


# ---------------------------------------------------------------------------
# 落盘（两段式的第二段）
# ---------------------------------------------------------------------------

def _job_path(book: str, fingerprint: str) -> Path:
    return data_dir() / "import_jobs" / f"{book}-{fingerprint[:12]}.json"


def _file_fingerprint(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_job(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_job(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def commit(book: str, file_path: str | Path, *, regex: str | None = None,
           encoding: str | None = None, mode: str = "draft",
           rebuild_memory: bool = False, confirm_warnings: bool = False,
           client=None) -> dict[str, Any]:
    if mode not in ("finalized", "draft"):
        raise ImportError_(f"mode 只能是 finalized / draft，实际 {mode!r}")
    text, enc = read_text(file_path, encoding)
    pattern = regex or guess_pattern(text)
    items = split_chapters(text, pattern)
    warns = _warnings(items)
    if warns and not confirm_warnings:
        raise ImportError_(
            f"预演有 {len(warns)} 条警告未确认（缺章/过短/重名），"
            "请带 confirm_warnings=true 再提交：" + "；".join(w["detail"] for w in warns[:3]))

    fp = _file_fingerprint(Path(file_path))
    job_file = _job_path(book, fp)
    job = _load_job(job_file)
    done: list[int] = list(job.get("done") or [])
    status = "finalized" if mode == "finalized" else "pending_finalize"
    imported = skipped = 0
    issues: list[dict[str, str]] = []

    for it in items:
        no = int(it["no"])
        if no in done and chapters.prose_path(book, no).exists():
            skipped += 1
            continue
        body = (it["body"] or "").strip() + "\n"
        if not body.strip():
            issues.append({"no": str(no), "issue": "空章节，已跳过"})
            continue
        fm = {"chapter_no": no, "version": 1,
              "status": "final" if status == "finalized" else "draft",
              "word_count": _chars(body), "finalized_at": None,
              "title": it["title"], "imported": True,
              "source_fingerprint": fp[:16]}
        chapters.write_prose(book, no, fm, body)
        statemachine.ensure_chapter_row(book, no, it["title"])
        statemachine.set_status_direct(book, no, status)
        db.set_imported(book_id=_book_id(book), chapter_no=no)
        if status == "finalized":
            db.add_snapshot(book_id=_book_id(book), chapter_no=no, version=1,
                            label="import", content=body)
        imported += 1
        done.append(no)
        _save_job(job_file, {"file_fingerprint": fp, "regex": pattern,
                             "done": sorted(set(done))})

    _append_outline_placeholders(book, items)
    if job_file.exists() and imported == 0:
        job_file.unlink(missing_ok=True)

    result: dict[str, Any] = {"imported": imported, "skipped": skipped,
                              "issues": issues, "encoding": enc,
                              "status": status, "warnings": warns}
    if rebuild_memory:
        from . import pipeline

        result["memory"] = pipeline.rebuild_memory_batch(
            book, [it["no"] for it in items], client=client)
    return result


def _book_id(book: str) -> int:
    from . import books

    return books.book_id(book)


def _append_outline_placeholders(book: str, items: list[dict[str, Any]]) -> None:
    """在大纲总表里标出"导入章无细纲"——不伪造细纲，也不让总表出现空洞。"""
    try:
        od = outline.read_outline(book)
        rows = list(od.get("chapter_rows") or [])
        known = {str(r.get("章", "")).strip() for r in rows}
        vols = od.get("volumes") or []
        added = False
        for it in items:
            no = int(it["no"])
            if str(no) in known:
                continue
            vol = "1"
            for v in vols:
                ch = v.get("chapters") or [0, 0]
                if len(ch) == 2 and ch[0] <= no <= ch[1]:
                    vol = str(v.get("no", "1"))
                    break
            rows.append({"章": str(no), "卷": vol, "本章目标": "（导入，无细纲）",
                         "冲突来源": "", "爽点/揭露": "", "钩子类型": "", "重要度★": ""})
            added = True
        if added:
            rows.sort(key=lambda r: int(re.sub(r"\D", "", str(r.get("章", "0")) or 0) or 0))
            fm = {k: v for k, v in od.items() if not k.startswith("_")}
            outline.write_outline(book, fm, rows, od.get("_rhythm", ""))
    except Exception as e:
        logger.warning("导入时写大纲占位失败（不影响正文导入）: %s", e)
