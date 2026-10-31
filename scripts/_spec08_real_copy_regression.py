"""A-T09 真实书副本回归（规格08 §6.2 A-T09）。

守的判据：**重建不改变章节、状态、内容，机检不新增误杀；原目录前后哈希不变。**

做法（严格只读原书）：
1. 原 home 下记录：全部文件 SHA-256、章号集合、每章状态、正文/细纲字段、
   记忆卡有无、字数、机检结论与命中规则（`pipeline._check` 纯计算，不写库/不转状态）、章纲行摘要；
2. 只把约定的这一本书复制到临时 home（`prompts/`、`lint/` 一并复制；`settings.json`
   只带 `mechcheck` 阈值，**不复制 API key**）；
3. 临时 home 里 `db.rebuild()`，用同一套快照函数重算并逐项比对；
4. 再按绝对路径复核原目录哈希未变（证明全程没写过原书）。

用法：python scripts/_spec08_real_copy_regression.py [书名] [证据json路径]
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOK = sys.argv[1] if len(sys.argv) > 1 else "流程测试书"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / ".pi" / "baseline" / "spec08-a-t09-real-copy.json"

sys.path.insert(0, str(ROOT))
from ainovel import paths  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files_at(base: Path) -> dict[str, str]:
    """按绝对路径对整本目录做字节快照（不依赖 AINOVEL_HOME）。"""
    return {str(p.relative_to(base)).replace("\\", "/"): _sha(p)
            for p in sorted(base.rglob("*")) if p.is_file()}


def snapshot(book: str) -> dict:
    """当前 home 下的书快照；只读文件与纯计算，不写盘、不改状态。"""
    from ainovel import books, chapters, pipeline, planning, statemachine

    cfg = books.load_config(book)
    wr = cfg["chapter_word_range"]
    nos = chapters.list_chapters(book)
    rows = planning.read_outline_full(book).get("chapter_rows") or []
    per: dict[str, dict] = {}
    for no in nos:
        prose = chapters.read_prose(book, no)
        try:
            status = statemachine.current_status(book, no)
        except Exception as error:  # 状态读不出来照实记录，不吞成"通过"
            status = f"<read-error:{error}>"
        contract = chapters.read_outline_contract(book, no)
        mem_path = chapters.memory_path(book, no)
        res = pipeline._check(book, no, wr)
        per[str(no)] = {
            "status": status,
            "words": chapters.count_words(prose["_body"]),
            "prose_status_field": prose.get("status"),
            "outline_confirmed": bool(contract.get("confirmed")),
            "outline_core_event": str(contract.get("core_event") or ""),
            "memory_present": mem_path.is_file(),
            "mech_verdict": res.verdict,
            "mech_blocks": sorted(h.rule for h in res.hits if h.level == "block"),
            "mech_warns": sorted(h.rule for h in res.hits if h.level == "warn"),
            "mech_ineffective": sorted(i.rule for i in res.ineffective),
        }
    return {
        "chapter_numbers": nos,
        "per_chapter": per,
        "outline_row_count": len(rows),
        "outline_rows_digest": hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
    }


def diff(before: dict, after: dict) -> list[str]:
    """逐项比对，返回人类可读的差异清单（空 = 完全一致）。"""
    problems: list[str] = []
    if before["files"] != after["files"]:
        added = sorted(set(after["files"]) - set(before["files"]))
        removed = sorted(set(before["files"]) - set(after["files"]))
        changed = sorted(k for k in before["files"].keys() & after["files"].keys()
                         if before["files"][k] != after["files"][k])
        problems.append(f"文件字节变化：新增 {added}，删除 {removed}，改动 {changed}")
    if before["snap"]["chapter_numbers"] != after["snap"]["chapter_numbers"]:
        problems.append(f"章号集合变化：{before['snap']['chapter_numbers']} → "
                        f"{after['snap']['chapter_numbers']}")
    for no, old in before["snap"]["per_chapter"].items():
        new = after["snap"]["per_chapter"].get(no)
        if new is None:
            problems.append(f"第{no}章在重建后缺失")
            continue
        for field in ("status", "words", "prose_status_field", "outline_confirmed",
                      "outline_core_event", "memory_present", "mech_verdict",
                      "mech_blocks", "mech_warns", "mech_ineffective"):
            if old[field] != new[field]:
                problems.append(f"第{no}章 {field}：{old[field]!r} → {new[field]!r}")
    if before["snap"]["outline_row_count"] != after["snap"]["outline_row_count"]:
        problems.append(f"章纲行数变化：{before['snap']['outline_row_count']} → "
                        f"{after['snap']['outline_row_count']}")
    if before["snap"]["outline_rows_digest"] != after["snap"]["outline_rows_digest"]:
        problems.append("章纲行内容摘要变化")
    return problems


def main() -> int:
    src = ROOT / "books" / BOOK
    if not src.is_dir():
        print(f"找不到书目录：{src}")
        return 2

    print(f"[1/4] 原 home 快照（只读）：{BOOK}")
    before = {"files": files_at(src), "snap": snapshot(BOOK)}
    print(f"      文件 {len(before['files'])} 个，章号 {len(before['snap']['chapter_numbers'])} 章，"
          f"章纲行 {before['snap']['outline_row_count']} 行")

    home = Path(tempfile.mkdtemp(prefix="ainovel-a-t09-"))
    print(f"[2/4] 复制到临时 home：{home}")
    shutil.copytree(ROOT / "prompts", home / "prompts")
    shutil.copytree(ROOT / "lint", home / "lint")
    (home / "books").mkdir()
    shutil.copytree(src, home / "books" / BOOK)
    from ainovel import settings as _settings

    (home / "data").mkdir()
    # 只带机检阈值，保证"机检口径一致"；不复制 settings.json 以免把 API key 带进临时目录
    (home / "data" / "settings.json").write_text(
        json.dumps({"mechcheck": _settings.load().get("mechcheck", {})},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    os.environ["AINOVEL_HOME"] = str(home)
    from ainovel import db

    db._CONN = None
    print("[3/4] 临时 home 内 db.rebuild()")
    rebuild = db.rebuild()
    after = {"files": files_at(home / "books" / BOOK), "snap": snapshot(BOOK)}
    problems = diff(before, after)

    print("[4/4] 复核原目录哈希未变")
    orig_after = files_at(src)
    original_untouched = orig_after == before["files"]

    evidence = {
        "book": BOOK,
        "temp_home": str(home),
        "original_files": len(before["files"]),
        "chapters": before["snap"]["chapter_numbers"],
        "rebuild": rebuild,
        "original_untouched": original_untouched,
        "problems": problems,
        "result": "pass" if (not problems and original_untouched) else "fail",
        "before": before["snap"],
        "after": after["snap"],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"      原目录未改动：{original_untouched}")
    if problems:
        print("      差异：")
        for p in problems:
            print("        -", p)
    print(f"结论：{evidence['result'].upper()}　证据：{OUT}")
    return 0 if evidence["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
