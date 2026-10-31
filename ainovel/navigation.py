"""（规格07 §5.1）章节规划、文件存在性与细纲生成只读预检。

规格08 §3.2：rolling 书在原门禁外追加 global→volume→unit→本章批次就绪检查，
原状态字段不重命名、legacy 书行为不变。
"""
from __future__ import annotations

from typing import Any

from . import chapters, conflicts, outline, statemachine


class OutlineGateError(RuntimeError):
    """带稳定 HTTP 原因码的细纲生成门禁错误。"""

    def __init__(self, code: str, message: str, http_status: int):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def planning_gate(book: str, no: int) -> dict[str, Any]:
    """rolling 书的规划链就绪检查（规格08 §6.3 P-T12）；legacy 一律放行。"""
    from . import planning

    try:
        info = planning.read_planning(book)
    except planning.PlanningInvalid as error:
        return {"allowed": False, "code": error.code, "message": f"无法判定：{error}"}
    if info["mode"] != "rolling":
        return {"allowed": True, "code": None, "message": "legacy"}
    state = planning.readiness(book)["layers"]
    if not (state.get("global") or {}).get("status") == "ready":
        return {"allowed": False, "code": "PLANNING_NOT_READY",
                "message": "全书粗纲尚未采纳，先到内容规划页完成全书层"}
    unit = next((u for u in info["planning"].get("units") or []
                 if (u.get("chapters") or [0, 0])[0] <= no <= (u.get("chapters") or [0, 0])[-1]),
                None)
    if unit is None:
        return {"allowed": False, "code": "PLANNING_NOT_READY",
                "message": f"第 {no} 章未映射到任何剧情单元，先在规划页生成并采纳章纲批次"}
    volume_key = f"volume:{unit.get('volume_no')}"
    volume_layer = next((lay for lay in state.get("volumes") or []
                         if lay.get("key") == volume_key), None)
    if not volume_layer or volume_layer.get("status") != "ready":
        return {"allowed": False, "code": "PLANNING_NOT_READY",
                "message": f"卷 {unit.get('volume_no')} 卷纲尚未采纳，先在规划页展开当前卷"}
    unit_layer = next((lay for lay in state.get("units") or []
                       if lay.get("key") == f"unit:{unit.get('id')}"), None)
    if not unit_layer or unit_layer.get("status") != "ready":
        return {"allowed": False, "code": "PLANNING_NOT_READY",
                "message": f"单元 {unit.get('name')} 详情尚未采纳，先在规划页细化该单元"}
    return {"allowed": True, "code": None, "message": "规划链就绪"}


def planned_numbers(book: str) -> tuple[set[int] | None, dict[str, str] | None]:
    """只认可读 OUTLINE.md 中的有效正整章号；缺文件与损坏严格区分。"""
    from .paths import book_dir

    if not (book_dir(book) / "OUTLINE.md").is_file():
        return set(), None
    try:
        rows = outline.read_outline(book)["chapter_rows"]
    except Exception as error:
        return None, {"code": "OUTLINE_INVALID", "message": f"大纲读取或解析失败：{error}"}
    numbers: set[int] = set()
    for row in rows:
        no = outline._row_no(row.get("章"))
        if no is not None and no > 0:
            numbers.add(no)
    return numbers, None


def inspection(book: str) -> dict[str, Any]:
    """规划章与正式三件套文件的并集；数据库残留行不参与。"""
    planned, error = planned_numbers(book)
    existing = set(chapters.list_chapters(book))
    numbers = sorted(existing | (planned or set()))
    return {
        "planned_numbers": planned,
        "chapter_numbers": numbers,
        "max_chapter": max(numbers, default=0),
        "planning_error": error,
    }


def outline_generation(book: str, no: int, planned: bool | None,
                       open_conflicts: int | None = None) -> dict[str, Any]:
    """瞬时预检，不创建章节、不执行 retry，也不代替实际管线门禁。"""
    if planned is None:
        return _denied("OUTLINE_INVALID", "大纲读取或解析失败，请先修复档案")
    if not planned:
        return _denied("OUTLINE_ROW_MISSING", f"OUTLINE.md 章节总表缺第 {no} 章行，请先补全大纲")
    pending = len(conflicts.list_open(book)) if open_conflicts is None else open_conflicts
    if pending:
        return _denied("CONFLICTS_PENDING", f"裁决队列非空（{pending} 条待处理），请先完成人工裁决")
    row = statemachine.get_row(book, no)
    status = row["status"] if row else "awaiting_outline"
    if status == "error":
        status = (row or {}).get("prev_status") or "awaiting_outline"
    if status not in ("awaiting_outline", "outline_draft"):
        return _denied("STATE_NOT_ALLOWED", f"第{no}章当前状态 {status} 不允许生成细纲")
    gate = planning_gate(book, no)
    if not gate["allowed"]:
        return _denied(gate["code"], gate["message"])
    return {"allowed": True, "code": None, "message": None}


def _denied(code: str, message: str) -> dict[str, Any]:
    return {"allowed": False, "code": code, "message": message}


def workbench_fields(book: str, no: int) -> dict[str, Any]:
    info = inspection(book)
    planned_set = info.pop("planned_numbers")
    planned = None if planned_set is None else no in planned_set
    exists = any(path.exists() for path in (
        chapters.outline_path(book, no), chapters.prose_path(book, no), chapters.memory_path(book, no)))
    return info | {
        "planned": planned,
        "exists": exists,
        "outline_generation": outline_generation(book, no, planned),
        "planning_gate": planning_gate(book, no),
    }


def require_generation(book: str, no: int) -> None:
    """API 实际执行前的稳定错误映射；管线仍会独立重查全部门禁。"""
    planned_set, error = planned_numbers(book)
    planned = None if planned_set is None else no in planned_set
    result = outline_generation(book, no, planned)
    if result["allowed"]:
        return
    statuses = {"OUTLINE_ROW_MISSING": 400, "OUTLINE_INVALID": 422,
                "CONFLICTS_PENDING": 409, "STATE_NOT_ALLOWED": 409,
                "PLANNING_NOT_READY": 409, "PLANNING_INVALID": 422,
                "PLANNING_SCHEMA_UNSUPPORTED": 422}
    message = result["message"]
    if error and result["code"] == "OUTLINE_INVALID":
        message = error["message"]
    raise OutlineGateError(result["code"], message, statuses[result["code"]])
