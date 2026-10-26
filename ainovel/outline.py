"""OUTLINE.md / PLOT_POINTS.md / LOCATIONS.md（规格01 §2.4-§2.6）。"""
from __future__ import annotations

import logging
import re
from typing import Any

from . import atomic, books, frontmatter as FM
from .mdtable import dump_table, parse_table

logger = logging.getLogger(__name__)

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
    return fm | {"_body": body, "chapter_rows": rows, "_rhythm": _parse_rhythm(body)}


def _parse_rhythm(body: str) -> str:
    """从 OUTLINE.md 正文抽出「## 爽点节奏总谱」一节（规格01 §2.4）。"""
    m = re.search(r"(?ms)^##\s*爽点节奏总谱\s*\n(.*?)(?=^##\s|\Z)", body)
    if not m:
        return ""
    text = m.group(1).strip()
    # 卷规划行是 write_outline 追加的，不属于节奏总谱正文
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("- 卷")]
    return "\n".join(lines).strip()


def read_rhythm(book: str) -> str:
    """只读节奏总谱（保存大纲时用于保留原有内容）。"""
    try:
        return _parse_rhythm(FM.validate_file(books.book_dir(book) / "OUTLINE.md",
                                              OUTLINE_SCHEMA)[1])
    except Exception:
        return ""


def write_outline(book: str, fm: dict[str, Any], rows: list[dict[str, str]], rhythm: str = "") -> None:
    # 剥离读接口专用的下划线字段（_body/_rhythm 等），绝不让它们进 frontmatter
    fm = {k: v for k, v in fm.items() if not k.startswith("_")}
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


def rollback_foreshadow_ops(book: str, chapter_no: int,
                            ops: list[dict[str, Any]] | None = None) -> dict[str, int]:
    """撤销第 N 章对台账的推进（记忆卡补做的幂等基线）。

    补做必须先回滚已成事实，否则 `advance`/`pay` 会被推第二次：
    伏笔状态是单向机（planted→building→paid），重放一次就报错或跳级。

    `ops` = 该章记忆卡里记的 `foreshadow_ops`（最可靠的依据）。没有卡时按台账
    痕迹推断：
    - 本章 `埋设章` 且确实由本章 plant 出来的 → 删行；推断不清时只回退到 planted
      （`埋设章==1` 这种"书开局就存在"的伏笔不能删）
    - 本章出现在 `推进章` → 摘掉；摘完没有别的推进章则回 planted
    - `备注` 含"第N章回收" → 去掉并回退到 building
    """
    rows = read_plot_points(book)
    tag = f"第{chapter_no}章"
    removed = reverted = advanced = 0
    kept: list[dict[str, str]] = []
    for r in rows:
        # 本章 plant 出来的行**不动**：plant 本身不写 `推进章`，而后续章节可能已经
        # 推进过它（`埋设章=1` 的伏笔常常在第 2、6、9 章被推进）。重跑同一章时
        # 由 `apply_foreshadow_ops` 的幂等 plant 兜住，不能在这里清空推进记录。
        chapters_str = str(r.get("推进章", "") or "")
        parts = [p.strip() for p in chapters_str.split(",") if p.strip()]
        if str(chapter_no) in parts:
            parts = [p for p in parts if p != str(chapter_no)]
            r["推进章"] = ",".join(parts)
            if r.get("状态") == "building" and not parts:
                r["状态"] = "planted"
            advanced += 1
        note = str(r.get("备注", "") or "")
        if f"{tag}回收" in note:
            r["备注"] = note.replace(f" {tag}回收", "").replace(f"{tag}回收", "").strip()
            if r.get("状态") == "paid":
                r["状态"] = "building"
            reverted += 1
        kept.append(r)
    if removed or advanced or reverted:
        write_plot_points(book, kept)
    return {"deleted": removed, "unadvanced": advanced, "unpaid": reverted}



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
                existing = by_id[fo_id]
                # 幂等：同一章重放 plant 不报错、不新建行（记忆卡补做会重跑同一张卡）。
                # 但**绝不回写「内容」**：台账是权威，V4 只是提议。实测第 3 章重跑时，
                # V4 把「苏婉给出透骨丹」误当 plant FO-003，而 FO-003 是「监察令」，
                # 若在这里覆盖内容，台账就把一条伏笔的定义悄悄换掉了——编号没变、
                # 下游引用全在，但意思已经错位，比直接报错危险得多。
                if str(existing.get("埋设章", "")).strip() == str(chapter_no):
                    logger.debug("%s: 第%s章已埋设过，跳过（内容保持不变）", fo_id, chapter_no)
                    continue
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
        # 时序护栏：补做历史章时，活跃清单里可能有"后面才埋"的伏笔，
        # V4 会顺手推进它——这会让台账出现"第 8 章推进第 12 章才埋的伏笔"。
        planted_at = str(row.get("埋设章", "") or "").strip()
        if planted_at.isdigit() and int(planted_at) > int(chapter_no):
            errors.append(f"{fo_id}: 该伏笔第{planted_at}章才埋设，第{chapter_no}章不能推进")
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


def write_locations(book: str, rows: list[dict[str, str]]) -> None:
    """整表写回地点档案（规格01 §2.6：名称/特征/方位·距离/首现章/关联剧情 5 列）。

    与 write_plot_points 同风格：全量序列化固定列 → atomic.write_text 原子落盘。
    行内多余键忽略、缺列补空串（dump_table 按 LOC_COLS 取值）。
    """
    table = dump_table(LOC_COLS, [[r.get(c, "") for c in LOC_COLS] for r in rows])
    atomic.write_text(books.book_dir(book) / "LOCATIONS.md", "# 地点档案\n\n" + table + "\n")


def upsert_location(book: str, name: str, feature: str = "", chapter_no: int = 0, plot: str = "") -> None:
    rows = read_locations(book)
    for r in rows:
        if r.get("名称") == name:
            return
    rows.append({"名称": name, "特征": feature, "方位/距离": "",
                 "首现章": str(chapter_no) if chapter_no else "", "关联剧情": plot})
    write_locations(book, rows)
