"""导入现存 txt（规格05 F-08）：预演不写盘，落盘前全量防覆盖预检。

断点身份绑定内容、切章正则、编码和模式，完成凭据保留以保证重复提交幂等。
历史章不伪造细纲，只在 OUTLINE 标注“（导入，无细纲）”。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

from . import atomic, chapters, db, frontmatter as FM, outline, statemachine
from .paths import data_dir

logger = logging.getLogger(__name__)

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "utf-16")

CHAPTER_PATTERNS = [
    r"^第[0-9零〇一二两三四五六七八九十百千万亿]+[章节回].*$",
    r"^[0-9]{1,4}[、.]\s*\S+",
    r"^Chapter\s+\d+.*$",
    r"^【第.+?章】.*$",
]

MIN_CHAPTER_CHARS = 500  # 低于此值提示 short（不阻断）

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}


class ImportError_(Exception):
    pass


# ---------------------------------------------------------------------------
# 编码与切章
# ---------------------------------------------------------------------------

def detect_encoding(raw: bytes) -> str | None:
    """（规格05 §9.4）BOM 优先；无 BOM 时依规格顺序尝试，不猜测编码。"""
    encodings = ("utf-16",) if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else ENCODINGS
    for enc in encodings:
        try:
            raw.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def read_text(path: str | Path, encoding: str | None = None) -> tuple[str, str]:
    p = Path(path)
    if not p.is_file():
        raise ImportError_(f"文件不存在: {p}")
    raw = p.read_bytes()
    enc = encoding or detect_encoding(raw)
    if not enc:
        raise ImportError_(f"编码无法识别（试过 {'/'.join(ENCODINGS)}），请手工指定：{p}")
    try:
        return raw.decode(enc), enc
    except (UnicodeDecodeError, LookupError) as e:
        raise ImportError_(f"用 {enc} 解码失败: {e}") from e


def cn2num(text: str) -> int | None:
    """（规格05 §9.4）解析阿拉伯及常用中文章号，支持千、万、亿和逐位数字。"""
    s = (text or "").strip().replace("两", "二")
    if not s:
        return None
    if s.isdecimal():
        return int(s)
    if all(c in _CN_DIGITS for c in s):
        return int("".join(str(_CN_DIGITS[c]) for c in s))
    if any(c not in _CN_DIGITS and c not in _CN_UNITS and c not in "万亿" for c in s):
        return None
    total = section = number = 0
    last_unit = 10000
    for c in s:
        if c in _CN_DIGITS:
            number = _CN_DIGITS[c]
        elif c in _CN_UNITS:
            unit = _CN_UNITS[c]
            if unit >= last_unit:
                return None
            section += (number or 1) * unit
            number = 0
            last_unit = unit
        else:
            section += number
            if c == "万":
                total += section * 10000
            else:
                total = (total + section) * 100000000
            section = number = 0
            last_unit = 10000
    return total + section + number


def guess_pattern(text: str) -> str:
    """挑命中章标题最多的正则（默认值，最终由用户在预演页确认）。"""
    best, best_n = CHAPTER_PATTERNS[0], 0
    for pat in CHAPTER_PATTERNS:
        n = len(re.findall(pat, text, flags=re.M))
        if n > best_n:
            best, best_n = pat, n
    return best


def split_chapters(text: str, pattern: str) -> list[dict[str, Any]]:
    """（规格05 §9.4）保留实际章号；无法解析时分配未被实际章号占用的备用号。"""
    lines = text.splitlines()
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise ImportError_(f"切章正则非法: {pattern!r}（{e}）") from e
    marks: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        if rx.match(line.strip()):
            marks.append((idx, line.strip()))

    if not marks:
        body = text.strip()
        return [{"no": 1, "title": "（未识别到章标题）", "chars": _chars(body),
                 "start_line": 1, "body": body, "parsed_no": None}]

    parsed_nos = [_parse_no(title) for _, title in marks]
    reserved = {n for n in parsed_nos if n is not None}
    fallback = 1
    out: list[dict[str, Any]] = []
    for i, (idx, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(lines)
        body = "\n".join(lines[idx + 1:end]).strip()
        parsed = parsed_nos[i]
        no = parsed
        if no is None:
            while fallback in reserved:
                fallback += 1
            no = fallback
            reserved.add(no)
        out.append({"no": no, "title": title[:60], "chars": _chars(body),
                    "start_line": idx + 1, "body": body, "parsed_no": parsed})
    return out


def _chars(s: str) -> int:
    return len(re.sub(r"\s+", "", s))


def _parse_no(title: str) -> int | None:
    m = re.search(r"第\s*([0-9零〇一二三四五六七八九十百千万亿两]+)\s*[章节回]", title)
    if m:
        no = cn2num(m.group(1))
    else:
        m = re.match(r"^([0-9]{1,4})[、.]|^Chapter\s+(\d+)", title, flags=re.I)
        no = int(m.group(1) or m.group(2)) if m else None
    return no if no is not None and no > 0 else None


def _warnings(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    warns: list[dict[str, str]] = []
    nums = [it["parsed_no"] for it in items if it.get("parsed_no") is not None]
    if nums:
        have = sorted(set(nums))
        if have[0] > 1:
            warns.append({"kind": "gap", "detail": f"缺第 1-{have[0] - 1} 章"})
        missing: list[int] = []
        for left, right in zip(have, have[1:]):
            missing.extend(range(left + 1, min(right, left + 1 + 10 - len(missing))))
            if len(missing) >= 10:
                break
        if missing:
            preview = "、".join(str(n) for n in missing)
            warns.append({"kind": "gap", "detail": f"缺第 {preview} 章"})
        dup = sorted(n for n, count in Counter(nums).items() if count > 1)
        if dup:
            warns.append({"kind": "duplicate_no", "detail": f"章号重复：{'、'.join(map(str, dup))}"})
    unparsed = [it for it in items if it.get("parsed_no") is None]
    if unparsed:
        assigned = "、".join(str(it["no"]) for it in unparsed[:10])
        warns.append({"kind": "unparsed_no", "detail": f"章号无法解析，已分配不冲突的备用章号：{assigned}"})
    dups = sorted(t for t, count in Counter(it["title"] for it in items).items() if count > 1)
    if dups:
        warns.append({"kind": "duplicate_title", "detail": f"标题相同：{'、'.join(dups[:5])}"})
    shorts = [it for it in items if it["chars"] < MIN_CHAPTER_CHARS]
    if shorts:
        preview = "、".join(f"第{it['no']}章({it['chars']}字)" for it in shorts[:5])
        warns.append({"kind": "short", "detail": f"正文过短：{preview}"})
    if items and items[0]["title"] == "（未识别到章标题）":
        warns.append({"kind": "no_heading", "detail": "没有识别到任何章标题，整篇将作为一章导入"})
    return warns


# ---------------------------------------------------------------------------
# 预演（不写盘）
# ---------------------------------------------------------------------------

def preview(file_path: str | Path, regex: str | None = None, *,
            encoding: str | None = None) -> dict[str, Any]:
    text, enc = read_text(file_path, encoding)
    pattern = regex or guess_pattern(text)
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

def _job_path(book: str, fingerprint: str, regex: str | None = None,
              encoding: str | None = None, mode: str | None = None) -> Path:
    """（规格05 §9.6）新凭据绑定分章配置；双参数调用仍可定位旧断点。"""
    identity = fingerprint
    if regex is not None or encoding is not None or mode is not None:
        payload = json.dumps([fingerprint, regex, encoding, mode], ensure_ascii=False)
        identity = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return data_dir() / "import_jobs" / f"{book}-{identity[:12]}.json"


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
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, OSError, UnicodeError):
        return {}


def _save_job(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _preflight(book: str, items: list[dict[str, Any]], fp: str,
               job: dict[str, Any], *, legacy: bool) -> tuple[set[int], set[int]]:
    """（规格05 §9.5-§9.6）先检查所有目标，不让后续冲突留下半份导入。"""
    recorded = job.get("done")
    done = {n for n in recorded if type(n) is int and n > 0} if isinstance(recorded, list) else set()
    existing = set(chapters.list_chapters(book))
    rows = {r["no"]: r for r in statemachine.list_chapters(book)}
    with db.tx() as conn:
        snapshots = {r["chapter_no"] for r in conn.execute(
            "SELECT DISTINCT chapter_no FROM snapshots WHERE book_id=?", (_book_id(book),))}
    existing.update(rows)
    existing.update(snapshots)
    skipped: set[int] = set()
    migrations: set[int] = set()
    conflicts: list[int] = []
    for it in items:
        no = int(it["no"])
        if no in done and chapters.prose_path(book, no).is_file():
            try:
                prose = chapters.read_prose(book, no)
            except (OSError, ValueError, FM.FrontmatterError):
                conflicts.append(no)
                continue
            if prose.get("chapter_no") != no:
                conflicts.append(no)
                continue
            source = prose.get("source_fingerprint")
            if source in (fp, fp[:16]) and (not legacy or all(
                    k in job for k in ("regex", "encoding", "mode"))):
                skipped.add(no)
                continue
            # 旧断点未存指纹/配置：仅在默认配置和原文逐章完全吻合时补齐来源凭据。
            row = rows.get(no, {})
            if (legacy and source in (None, fp, fp[:16]) and prose.get("version") == 1
                    and prose.get("status") == "draft"
                    and prose["_body"].strip() == it["body"].strip()
                    and row.get("title") in ("", it["title"])
                    and row.get("status") == "pending_finalize"
                    and row.get("current_version") == 1
                    and no not in snapshots
                    and not any(p.exists() for p in (chapters.outline_path(book, no),
                                                    chapters.memory_path(book, no),
                                                    chapters.draft_path(book, no)))):
                migrations.add(no)
                skipped.add(no)
                continue
        if no in existing or chapters.draft_path(book, no).exists():
            conflicts.append(no)
    if conflicts:
        raise ImportError_("目标章节已存在或断点来源不匹配，拒绝覆盖：第 "
                           + "、".join(map(str, conflicts)) + " 章")
    return skipped, migrations


def commit(book: str, file_path: str | Path, *, regex: str | None = None,
           encoding: str | None = None, mode: str = "draft",
           rebuild_memory: bool = False, confirm_warnings: bool = False,
           client: Any = None) -> dict[str, Any]:
    """（规格05 F-08）预检后落盘；重复实际章号不能通过确认警告强行导入。"""
    if mode not in ("finalized", "draft"):
        raise ImportError_(f"mode 只能是 finalized / draft，实际 {mode!r}")
    text, enc = read_text(file_path, encoding)
    pattern = regex or guess_pattern(text)
    items = split_chapters(text, pattern)
    duplicates = sorted(no for no, count in Counter(it["no"] for it in items).items() if count > 1)
    if duplicates:
        raise ImportError_("章号重复，拒绝导入：" + "、".join(map(str, duplicates)))
    warns = _warnings(items)
    if warns and not confirm_warnings:
        raise ImportError_(
            f"预演有 {len(warns)} 条警告未确认（缺章/过短/重名/章号无法解析），"
            "请带 confirm_warnings=true 再提交：" + "；".join(w["detail"] for w in warns[:3]))

    fp = _file_fingerprint(Path(file_path))
    config = {"file_fingerprint": fp, "regex": pattern, "encoding": enc, "mode": mode}
    job_file = _job_path(book, fp, pattern, enc, mode)
    job = _load_job(job_file)
    if any(job.get(k) != v for k, v in config.items()):
        job = {}
    legacy = False
    if not job and not job_file.exists():
        old = _load_job(_job_path(book, fp))
        # 缺配置的旧断点只可解释为原自动切章、自动编码、draft，不推断其他模式。
        defaults = {"regex": guess_pattern(text), "encoding": enc if encoding is None else None,
                    "mode": "draft"}
        if old.get("file_fingerprint") == fp and all(
                old.get(k, defaults.get(k)) == v for k, v in config.items()):
            job, legacy = old, True
    skipped, migrations = _preflight(book, items, fp, job, legacy=legacy)
    done = skipped - migrations
    status = "finalized" if mode == "finalized" else "pending_finalize"
    imported = 0
    issues: list[dict[str, str]] = []
    book_id = _book_id(book)

    for it in items:
        no = int(it["no"])
        if no in skipped:
            if no in migrations:
                # 旧调用可能先写正文再建行；只补来源元数据，不改动正文内容。
                prose = chapters.read_prose(book, no)
                original_body = prose.pop("_body")
                prose.update(title=it["title"], imported=True, source_fingerprint=fp)
                FM.validate(prose, chapters.PROSE_SCHEMA)
                atomic.write_text(chapters.prose_path(book, no), FM.dump(prose, original_body))
                with db.tx() as conn:
                    conn.execute("UPDATE chapters SET title=?, updated_at=? WHERE book_id=? AND no=?",
                                 (it["title"], db.now(), book_id, no))
                db.set_imported(book_id=book_id, chapter_no=no)
                done.add(no)
                _save_job(job_file, config | {"done": sorted(done)})
            continue
        body = (it["body"] or "").strip() + "\n"
        if not body.strip():
            issues.append({"no": str(no), "issue": "空章节，已跳过"})
            continue
        fm = {"chapter_no": no, "version": 1,
              "status": "final" if status == "finalized" else "draft",
              "word_count": _chars(body), "finalized_at": None,
              "title": it["title"], "imported": True, "source_fingerprint": fp}
        # write_prose 可能更新检查结论和修订号，必须先建行，避免丢失标题。
        statemachine.ensure_chapter_row(book, no, it["title"])
        chapters.write_prose(book, no, fm, body)
        statemachine.set_current_version(book, no, 1)
        statemachine.set_status_direct(book, no, status)
        db.set_imported(book_id=book_id, chapter_no=no)
        if status == "finalized":
            db.add_snapshot(book_id=book_id, chapter_no=no, version=1,
                            label="import", content=body)
        imported += 1
        done.add(no)
        _save_job(job_file, config | {"done": sorted(done)})

    if done and not job_file.exists():
        _save_job(job_file, config | {"done": sorted(done)})
    _append_outline_placeholders(book, [it for it in items if it["no"] in done])
    # 完成凭据不能删除：否则第三次提交会失去来源证明，甚至重写历史正文。
    result: dict[str, Any] = {"imported": imported, "skipped": len(skipped),
                              "issues": issues, "encoding": enc,
                              "status": status, "warnings": warns}
    if rebuild_memory:
        from . import pipeline

        result["memory"] = pipeline.rebuild_memory_batch(
            book, [it["no"] for it in items if it["no"] in done], client=client)
    return result


def _book_id(book: str) -> int:
    from . import books

    return books.book_id(book)


def _append_outline_placeholders(book: str, items: list[dict[str, Any]]) -> None:
    """在大纲总表里标出\"导入章无细纲\"——不伪造细纲，也不让总表出现空洞。"""
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
