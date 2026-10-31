"""G5 采纳影响预览（规格08 §0.1 08-A03；判据 §6.2 A-T04 / A-T06）。

纯计算模块：只读正式档案，不写任何文件、不记凭据、不调用模型。

三条必须守住的语义（规格08 §5 失败模式）：

1. 「已有正文章数」按 `chapters/*_正文.md` 的**真实章号**去重统计。章节总表七列里本来
   就没有状态列，旧实现按 `row["状态"]` 统计恒为 0——这是 A-T04 要修的缺陷。
2. 「受影响章号」= 本次替换的旧章行号 ∪ 新章行号，与正文文件章号取交集；正文不会被
   采纳动作重写，只如实计数。
3. 正式文件读不出来时对应字段返回 ``None`` 并给出中文 warning，**不假计 0**
   （0 与「无法判定」是两件事）。

预览的 OUTLINE/PLOT_POINTS 全文一律走 ``outline.render_*``，与真正落盘同一处渲染，
保证 diff 里看到的就是将要写入的内容。
"""
from __future__ import annotations

import re
from typing import Any

from . import chapters, outline
from .mdtable import parse_table

CHAPTER_COLS = outline.CHAPTER_COLS


# ---------------------------------------------------------------------------
# G5 输出解析（与 adopt 共用，防止预览与采纳两套归一化逻辑漂移）
# ---------------------------------------------------------------------------

def normalize_rows(text: str) -> list[dict[str, str]]:
    """章节总表归一化：章/卷列抽数字（"第1章/第一卷" → 1）。"""
    _cols, rows = parse_table(text, "本章目标")
    out: list[dict[str, str]] = []
    for r in rows:
        m = re.search(r"\d+", str(r.get("章", "")))
        if not m:
            continue
        row = dict(r)
        vm = re.search(r"\d+", str(r.get("卷", "")))
        row["章"] = m.group()
        if vm:
            row["卷"] = vm.group()
        out.append(row)
    return out


def normalize_volumes(text: str, lo: int, hi: int) -> list[dict[str, Any]]:
    """卷规划归一化：卷号抽数字；起止章抽两个数字，缺失用总表范围兜底。"""
    _cols, rows = parse_table(text, "卷号")
    volumes: list[dict[str, Any]] = []
    for i, r in enumerate(rows, 1):
        nums = [int(x) for x in re.findall(r"\d+", str(r.get("起止章", "")))]
        m = re.search(r"\d+", str(r.get("卷号", "")))
        ch = (nums + [lo, hi])[:2] if len(nums) >= 2 else [lo, hi]
        volumes.append({"no": m.group() if m else str(i), "name": r.get("卷名", ""),
                        "chapters": ch, "core_conflict": r.get("核心冲突", ""),
                        "goal": r.get("本卷目标", ""), "climax": r.get("卷末高潮", ""),
                        "hook_out": r.get("卷末钩子(出卷悬念)", "")})
    return volumes


def parse_rhythm(text: str) -> str:
    m = re.search(r"(?ms)^##\s*爽点节奏总谱\s*\n(.*?)(?=^##|\Z)", text)
    return m.group(1).strip() if m else ""


def parse_foreshadows(text: str) -> list[dict[str, str]]:
    _cols, rows = parse_table(text, "编号")
    return rows


def merge_foreshadows(
    book: str, frows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], dict[str, str], int, int]:
    """把 G5 伏笔行合并进现有台账（规格02 §4 编号自动分配 + 永不静默覆盖）。

    匹配键 = 「内容」文本（去空白）。命中 → 沿用原编号与已有状态/埋设章/推进章/预计回收章，
    只补空字段；未命中 → 分配 `max(现有FO数字)+1` 递增的新编号。

    返回 (合并后台账行, {G5编号: FO编号}, 新增数, 命中保留数)。只读文件，不落盘。
    """
    try:
        existing = outline.read_plot_points(book)
    except FileNotFoundError:
        # 台账文件缺失（手工删过/老目录升级）：按空台账处理，G5 至少能建起一份
        existing = []
    merged = [dict(r) for r in existing]
    # 内容 → 合并副本（首条优先；空内容不参与匹配，避免互相误吞）
    by_content: dict[str, dict[str, str]] = {}
    next_no = 1
    for row in merged:
        fid = str(row.get("编号", ""))
        m = re.search(r"(\d+)", fid)
        if m:
            next_no = max(next_no, int(m.group(1)) + 1)
        content = str(row.get("内容", "")).strip()
        if content and content not in by_content:
            by_content[content] = row

    id_map: dict[str, str] = {}
    added = kept = 0
    for r in frows:
        g5_id = str(r.get("编号", "")).strip()
        content = str(r.get("内容", "")).strip()
        hit = by_content.get(content) if content else None
        if hit is not None:
            # 已有：保留原编号/状态/章次，只补空字段
            for col in ("埋设章", "推进章", "预计回收章", "备注"):
                if not str(hit.get(col, "")).strip() and str(r.get(col, "")).strip():
                    hit[col] = r.get(col, "")
            id_map[g5_id] = str(hit.get("编号", ""))
            kept += 1
            continue
        new_id = f"FO-{next_no:03d}"
        next_no += 1
        row = {"编号": new_id, "内容": content, "埋设章": r.get("埋设章", ""),
               "推进章": r.get("推进章", ""), "预计回收章": r.get("预计回收章", ""),
               "状态": "planted", "备注": r.get("备注", "")}
        merged.append(row)
        if content:
            by_content[content] = row
        id_map[g5_id] = new_id
        added += 1
    return merged, id_map, added, kept


# ---------------------------------------------------------------------------
# 已写正文（真实章号，不看表内状态列）
# ---------------------------------------------------------------------------

def written_numbers(book: str) -> tuple[list[int] | None, list[str]]:
    """已有正文的真实章号（去重升序）。目录不可读返回 (None, warnings)。

    口径：`NNN_正文.md` 存在即算已写（**含未定稿 status**）；流式中断留下的
    `NNN_正文.draft.md` 也算，因为它同样是"已写、不能被规划采纳覆盖"的内容。
    两者按章号去重——同一章既有正式正文又有半成品草稿只计 1 章。
    """
    try:
        folder = chapters.chapters_dir(book)
        if not folder.is_dir():
            return [], []
        names = [p.name for p in folder.glob("*.md")]
    except OSError as error:  # pragma: no cover - 目录权限类异常
        return None, [f"章节目录不可读（{type(error).__name__}），已有正文章数无法判定"]
    return sorted({int(m.group(1)) for n in names
                   if (m := re.fullmatch(r"([0-9]+)_正文(?:\.draft)?\.md", n))}), []


def _row_numbers(rows: list[dict[str, str]]) -> list[int]:
    nums = []
    for r in rows:
        m = re.search(r"\d+", str(r.get("章", "")))
        if m:
            nums.append(int(m.group()))
    return sorted(set(nums))


def _row_signature(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(str(row.get(c, "")).strip() for c in CHAPTER_COLS if c != "章")


def _signatures(rows: list[dict[str, str]]) -> dict[int, tuple[str, ...]]:
    """章号 → 该行除章号外七列的内容签名（用于判定"内容有变化"）。"""
    out: dict[int, tuple[str, ...]] = {}
    for r in rows:
        m = re.search(r"\d+", str(r.get("章", "")))
        if m:
            out[int(m.group())] = _row_signature(r)
    return out


# ---------------------------------------------------------------------------
# 预览主入口
# ---------------------------------------------------------------------------

def preview(book: str, content: str) -> dict[str, Any]:
    """计算一次 G5 采纳的完整影响（纯读）。结构见规格08 A-T04。

    失败语义：OUTLINE.md 读不出来 → ``old_readable=False``，旧章行按空参与 diff，
    同时给 warning；不把"读不出来"当成"旧大纲为空"。
    """
    warnings: list[str] = []
    new_rows = normalize_rows(content)
    if not new_rows:
        raise ValueError("章节总表未解析到任何有效行")
    lo, hi = int(new_rows[0]["章"]), max(int(x["章"]) for x in new_rows)

    # —— 旧大纲 ——
    old_readable = True
    old_rows: list[dict[str, str]] = []
    old_outline_text = ""
    try:
        old_fm = outline.read_outline(book)
        old_rows = old_fm["chapter_rows"]
        old_outline_text = old_fm["_body"]
        old_rhythm = old_fm.get("_rhythm", "")
        old_volumes = old_fm.get("volumes", [])
        old_logline = str(old_fm.get("logline") or "")
        old_ending = str(old_fm.get("ending_direction") or "")
    except Exception as error:
        old_readable = False
        old_rhythm, old_volumes, old_logline, old_ending = "", [], "", ""
        warnings.append(f"OUTLINE.md 读取失败（{type(error).__name__}），旧章行与差异无法判定")

    # —— 伏笔台账影子合并（只读） ——
    frows = parse_foreshadows(content)
    try:
        pp_old_rows = outline.read_plot_points(book)
        pp_readable = True
    except FileNotFoundError:
        pp_old_rows, pp_readable = [], True
    except Exception as error:
        pp_old_rows, pp_readable = [], False
        warnings.append(f"PLOT_POINTS.md 读取失败（{type(error).__name__}），伏笔基线无法判定")
    merged, id_map, fo_added, fo_kept = merge_foreshadows(book, frows)

    # —— 章号集合运算 ——
    old_nos = _row_numbers(old_rows)
    new_nos = _row_numbers(new_rows)
    old_sig = _signatures(old_rows)
    new_sig = _signatures(new_rows)
    added = sorted(set(new_nos) - set(old_nos))
    removed = sorted(set(old_nos) - set(new_nos))
    changed = sorted(n for n in set(new_nos) & set(old_nos)
                     if old_sig.get(n) != new_sig.get(n))

    written, w_warn = written_numbers(book)
    warnings.extend(w_warn)
    written_known = written is not None
    written_set = set(written or [])
    touched = set(old_nos) | set(new_nos)
    affected = sorted(touched & written_set)

    # —— 将写入的全文（同一渲染，供 diff） ——
    volumes = normalize_volumes(content, lo, hi) or [
        {"no": "1", "name": "", "chapters": [lo, hi], "core_conflict": "",
         "goal": "", "climax": "", "hook_out": ""}]
    rhythm = parse_rhythm(content)
    logline = old_logline.strip() or "（待填）"
    new_outline_text = outline.render_outline(
        {"logline": logline, "ending_direction": old_ending, "volumes": volumes},
        new_rows, rhythm)
    old_plot_text = outline.render_plot_points(pp_old_rows) if pp_readable else None
    new_plot_text = outline.render_plot_points(merged) if pp_readable else None

    return {
        "ok": True,
        "old_readable": old_readable,
        "plot_readable": pp_readable,
        "old_rows": old_rows,
        "new_rows": new_rows,
        "old_row_numbers": old_nos,
        "new_row_numbers": new_nos,
        "added_numbers": added,
        "removed_numbers": removed,
        "changed_numbers": changed,
        "written": {"numbers": sorted(written_set), "total": len(written_set) if written_known else None,
                    "known": written_known},
        "affected_numbers": affected,
        "affected_total": len(affected) if written_known else None,
        "volumes": len(volumes),
        "chapter_rows": len(new_rows),
        "foreshadows_added": fo_added,
        "foreshadows_kept": fo_kept,
        "foreshadow_rows": len(frows),
        "id_map": id_map,
        "outline_text_old": old_outline_text,
        "outline_text_new": new_outline_text,
        "plot_points_text_old": old_plot_text,
        "plot_points_text_new": new_plot_text,
        "warnings": warnings,
    }
