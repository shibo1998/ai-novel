"""OUTLINE.md / PLOT_POINTS.md / LOCATIONS.md（规格01 §2.4-§2.6）。"""
from __future__ import annotations

import re
from typing import Any

from . import atomic, books, frontmatter as FM
from .mdtable import dump_table, parse_table

OUTLINE_SCHEMA: FM.Schema = {
    "required": ["logline"],
    "types": {"logline": str, "ending_direction": str, "volumes": list},
}

CHAPTER_COLS = ["章", "卷", "本章目标", "冲突来源", "爽点/揭露", "钩子类型", "重要度★"]


# ---------------------------------------------------------------------------
# OUTLINE.md
# ---------------------------------------------------------------------------

def read_outline(book: str) -> dict[str, Any]:
    fm, body = FM.validate_file(books.book_dir(book) / "OUTLINE.md", OUTLINE_SCHEMA)
    _, rows = parse_table(body, "本章目标")
    return fm | {"_body": body, "chapter_rows": rows}


def write_outline(book: str, fm: dict[str, Any], rows: list[dict[str, str]], rhythm: str = "") -> None:
    FM.validate(fm, OUTLINE_SCHEMA)
    vols = fm.get("volumes") or []
    vol_lines = []
    for v in vols:
        ch = v.get("chapters") or [1, 1]
        vol_lines.append(
            f"- 卷{v.get('no','')}`{v.get('name','')}` 章{ch[0]}-{ch[1]}："
            f"核心冲突={v.get('core_conflict','')}｜目标={v.get('goal','')}｜"
            f"高潮={v.get('climax','')}｜卷末钩子={v.get('hook_out','')}"
        )
    table = dump_table(CHAPTER_COLS, [[r.get(c, "") for c in CHAPTER_COLS] for r in rows])
    body = (
        "## 章节总表\n\n" + table + "\n\n## 爽点节奏总谱\n\n" + (rhythm or "（待规划）") + "\n"
        + ("\n".join(vol_lines) + "\n" if vol_lines else "")
    )
    atomic.write_text(books.book_dir(book) / "OUTLINE.md", FM.dump(fm, body))


def _row_no(val: Any) -> int | None:
    """章号列容错抽取：'3'、'第3章'、'第3章（上）' → 3。"""
    m = re.search(r"\d+", str(val or ""))
    return int(m.group()) if m else None


def chapter_row(book: str, no: int) -> dict[str, str] | None:
    for r in read_outline(book)["chapter_rows"]:
        if _row_no(r.get("章")) == no:
            return r
    return None


def total_chapters(book: str) -> int:
    rows = read_outline(book)["chapter_rows"]
    nos = [n for n in (_row_no(r.get("章")) for r in rows) if n]
    return max(nos) if nos else 0


# ---------------------------------------------------------------------------
# PLOT_POINTS.md（伏笔台账 + 状态机校验，规格01 §2.5）
# ---------------------------------------------------------------------------

PP_COLS = ["编号", "内容", "埋设章", "推进章", "预计回收章", "状态", "备注"]
PP_STATUSES = ["planted", "building", "paid"]


def read_plot_points(book: str) -> list[dict[str, str]]:
    text = (books.book_dir(book) / "PLOT_POINTS.md").read_text(encoding="utf-8")
    _, rows = parse_table(text, "编号")
    return rows


def write_plot_points(book: str, rows: list[dict[str, str]]) -> None:
    table = dump_table(PP_COLS, [[r.get(c, "") for c in PP_COLS] for r in rows])
    atomic.write_text(books.book_dir(book) / "PLOT_POINTS.md",
                      "# 伏笔台账\n\n" + table + "\n")


def validate_transitions(old: list[dict[str, str]], new: list[dict[str, str]]) -> list[str]:
    """校验状态迁移：只允许 planted→building→paid 单向；paid 不得回退（防复活）。"""
    errors: list[str] = []
    old_map = {r.get("编号"): r.get("状态", "") for r in old}
    for r in new:
        fid = r.get("编号", "")
        new_st = r.get("状态", "")
        if new_st and new_st not in PP_STATUSES:
            errors.append(f"{fid}: 非法状态 {new_st}")
            continue
        old_st = old_map.get(fid)
        if old_st and new_st:
            order = {s: i for i, s in enumerate(PP_STATUSES)}
            if order[new_st] < order[old_st]:
                errors.append(f"{fid}: 状态回退 {old_st}→{new_st}（paid 后不得复活）")
    return errors


def overdue_warnings(rows: list[dict[str, str]], current_chapter: int, grace: int = 30) -> list[str]:
    """planted 超过 30 章未进 building → 超期预警。"""
    warns = []
    for r in rows:
        if r.get("状态") != "planted":
            continue
        try:
            planted = int(r.get("埋设章") or 0)
        except ValueError:
            continue
        if planted and current_chapter - planted > grace:
            warns.append(f"{r.get('编号')}: 第{planted}章埋设，已 {current_chapter - planted} 章未推进（超30章预警）")
    return warns


def active_foreshadows(book: str) -> list[dict[str, str]]:
    return [r for r in read_plot_points(book) if r.get("状态") in ("planted", "building")]


def apply_foreshadow_ops(
    book: str, chapter_no: int, ops: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """把细纲/记忆卡的 foreshadow_ops 应用到台账。

    返回 (错误列表——非法引用/状态冲突, 预警列表)。出错时整体不写盘。
    """
    rows = read_plot_points(book)
    by_id = {r.get("编号"): r for r in rows}
    errors: list[str] = []
    for op in ops:
        fo_id = op.get("fo_id", "")
        kind = op.get("op", "")
        if kind not in ("plant", "advance", "pay"):
            errors.append(f"非法伏笔操作 {kind}（FO={fo_id}）")
            continue
        if kind == "plant":
            if fo_id in by_id:
                errors.append(f"{fo_id}: 台账已存在，不能重复埋设")
                continue
            rows.append({
                "编号": fo_id, "内容": op.get("note", ""), "埋设章": str(chapter_no),
                "推进章": "", "预计回收章": op.get("expected_pay", ""), "状态": "planted", "备注": "",
            })
            continue
        row = by_id.get(fo_id)
        if row is None:
            errors.append(f"{fo_id}: 台账中不存在（advance/pay 必须引用已有编号）")
            continue
        st = row.get("状态", "")
        if kind == "advance":
            if st == "paid":
                errors.append(f"{fo_id}: 已回收，不能再推进")
                continue
            old_rows = [dict(r) for r in rows]
            row["状态"] = "building"
            row["推进章"] = (row.get("推进章", "") + ("," if row.get("推进章") else "") + str(chapter_no))
            errs = validate_transitions(old_rows, rows)
            errors.extend(errs)
        elif kind == "pay":
            if st != "building":
                errors.append(f"{fo_id}: 状态为 {st}，回收要求 building")
                continue
            row["状态"] = "paid"
            row["备注"] = (row.get("备注", "") + f" 第{chapter_no}章回收").strip()
    if errors:
        return errors, []
    write_plot_points(book, rows)
    warns = overdue_warnings(rows, chapter_no)
    return [], warns


# ---------------------------------------------------------------------------
# LOCATIONS.md
# ---------------------------------------------------------------------------

LOC_COLS = ["名称", "特征", "方位/距离", "首现章", "关联剧情"]


def read_locations(book: str) -> list[dict[str, str]]:
    text = (books.book_dir(book) / "LOCATIONS.md").read_text(encoding="utf-8")
    _, rows = parse_table(text, "名称")
    return rows


def upsert_location(book: str, name: str, feature: str = "", chapter_no: int = 0, plot: str = "") -> None:
    rows = read_locations(book)
    for r in rows:
        if r.get("名称") == name:
            return
    rows.append({"名称": name, "特征": feature, "方位/距离": "",
                 "首现章": str(chapter_no) if chapter_no else "", "关联剧情": plot})
    table = dump_table(LOC_COLS, [[r.get(c, "") for c in LOC_COLS] for r in rows])
    atomic.write_text(books.book_dir(book) / "LOCATIONS.md", "# 地点档案\n\n" + table + "\n")
