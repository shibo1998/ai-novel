"""内容规划数据层（规格08 §2.2～§2.3，阶段B第一步）。

只负责 OUTLINE.md 里**可选**的 `planning` 元数据：解析与校验、分层内容指纹、就绪状态、
规划树、估算与保护集。三条边界：

1. **文件是真相**：本模块读 OUTLINE.md / PLOT_POINTS.md / 章节文件，不写 DB、不新增表列。
2. **不加第二个状态机**：`missing/unadopted/ready/stale/invalid` 是**规划读模型**，
   不进 `statemachine.STATES`，也不影响章节状态。
3. **缺字段就是 legacy**：缺 `planning` 的书继续走原流程，本模块不替它写 enabled、
   不自动采纳、不改任何旧稿（规格08 §4.1）。

指纹投影集中定义在本模块（规格08 §2.3 要求"实现必须集中定义投影，不由前后端各自拼散列"）：
前端只展示服务端算出的就绪状态，不自己算哈希。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from . import books, chapters, outline

SCHEMA_VERSION = 1
STATUS_MESSAGES = {
    "missing": "尚未建立（空骨架）",
    "unadopted": "待采纳（内容已存在但未人工确认）",
    "ready": "已采纳/就绪",
    "stale": "上游变化待复核（上游已改，本层需重新确认）",
    "invalid": "无法判定（结构损坏，需修复）",
}


class PlanningInvalid(Exception):
    """规划元数据结构不合法。code 为规格08 §3.2 的稳定错误码。"""

    def __init__(self, message: str, code: str = "PLANNING_INVALID") -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# 读取与校验（规格08 §2.2 字段约束）
# ---------------------------------------------------------------------------

def read_outline_full(book: str) -> dict[str, Any]:
    """读 OUTLINE.md 的 frontmatter + 正文；文件缺失/损坏抛异常（由调用方决定降级）。"""
    return outline.read_outline(book)


def read_planning(book: str) -> dict[str, Any]:
    """返回 `{mode, schema_version, planning}`。缺 planning → mode=legacy。

    - 文件不存在 → legacy（新书/空书没有规划层，不许凭空启用读写）。
    - 未知 schema_version **不降级**为 legacy（规格08 §2.2 约束1），直接抛
      `PLANNING_SCHEMA_UNSUPPORTED`，避免把"读不懂"当成"没有规划"。
    - OUTLINE.md 本身损坏 → `OUTLINE_INVALID`（无法判定，不是零规划成功）。
    """
    try:
        fm = read_outline_full(book)
    except FileNotFoundError:
        return {"mode": "legacy", "schema_version": None, "planning": None}
    except Exception as error:
        raise PlanningInvalid(f"OUTLINE.md 读取或解析失败：{error}",
                              code="OUTLINE_INVALID")
    raw = fm.get("planning")
    if raw is None:
        return {"mode": "legacy", "schema_version": None, "planning": None}
    if not isinstance(raw, dict):
        raise PlanningInvalid("planning 必须是映射（字典），当前是 "
                             f"{type(raw).__name__}")
    version = raw.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise PlanningInvalid(f"不支持的 planning.schema_version={version!r}",
                              code="PLANNING_SCHEMA_UNSUPPORTED")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise PlanningInvalid("planning.enabled 必须是布尔值，不接受字符串 "
                              f"{enabled!r}")
    planning = {
        "schema_version": SCHEMA_VERSION,
        "enabled": enabled,
        "global": raw.get("global") or {},
        "units": raw.get("units") or [],
        "adoptions": raw.get("adoptions") or {},
    }
    if not isinstance(planning["units"], list):
        raise PlanningInvalid("planning.units 必须是列表")
    if not isinstance(planning["adoptions"], dict):
        raise PlanningInvalid("planning.adoptions 必须是映射")
    for unit in planning["units"]:
        _check_unit(unit)
    return {"mode": "rolling" if enabled else "legacy",
            "schema_version": SCHEMA_VERSION, "planning": planning}


def _check_unit(unit: Any) -> None:
    if not isinstance(unit, dict):
        raise PlanningInvalid("planning.units 的每一项必须是映射")
    uid = str(unit.get("id") or "")
    if not re.fullmatch(r"U-[0-9a-f]{32}", uid):
        raise PlanningInvalid(f"单元 id 必须是 U-+32位小写hex，当前 {uid!r}",
                              code="PLANNING_RANGE_INVALID")
    rng = unit.get("chapters")
    if rng is None:
        return
    if not (isinstance(rng, list) and len(rng) == 2
            and all(isinstance(x, int) and x > 0 for x in rng) and rng[0] <= rng[1]):
        raise PlanningInvalid(f"单元 {uid} 的 chapters 必须是 [a,b] 且 a≤b，当前 {rng!r}",
                              code="PLANNING_RANGE_INVALID")


# ---------------------------------------------------------------------------
# 指纹投影（规格08 §2.3：各层取哪些字段必须集中定义）
# ---------------------------------------------------------------------------

def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest(payload: Any) -> str:
    """规范化结构 JSON 的完整 SHA-256（UTF-8、键排序、固定分隔符）。"""
    return hashlib.sha256(_canonical(payload)).hexdigest()


def project_global(fm: dict[str, Any], planning: dict[str, Any]) -> dict[str, Any]:
    """全书层：root logline/ending_direction + global + 各卷 no/name/direction/估算。

    **不取**已展开卷的详细字段、单元详情与章节表——深化下层不能反向改变全书指纹。
    """
    g = planning.get("global") or {}
    return {
        "logline": str(fm.get("logline") or "").strip(),
        "ending_direction": str(fm.get("ending_direction") or "").strip(),
        "reader_promise": str(g.get("reader_promise") or ""),
        "selling_points": [str(x) for x in (g.get("selling_points") or [])],
        "mainline": str(g.get("mainline") or ""),
        "stage_boundaries": [str(x) for x in (g.get("stage_boundaries") or [])],
        "reserved_reveals": [str(x) for x in (g.get("reserved_reveals") or [])],
        "estimated_chapters_override": g.get("estimated_chapters_override"),
        "volumes": [
            {"no": str(v.get("no")), "name": str(v.get("name") or ""),
             "direction": str(v.get("direction") or ""),
             "estimated_chapters": v.get("estimated_chapters")}
            for v in (fm.get("volumes") or [])
        ],
    }


def project_volume(fm: dict[str, Any], planning: dict[str, Any], volume_no: str) -> dict[str, Any]:
    """卷层：本卷详细字段 + 每个单元骨架（id/name/chapters）。

    **不取**单元 detail_level 与详情——单元深化不改变卷层指纹。
    """
    vol = next((v for v in (fm.get("volumes") or [])
                if str(v.get("no")) == str(volume_no)), None)
    if vol is None:
        raise PlanningInvalid(f"卷 {volume_no} 不存在")
    skeletons = [
        {"id": str(u.get("id")), "name": str(u.get("name") or ""),
         "chapters": u.get("chapters") or []}
        for u in planning.get("units") or []
        if str(u.get("volume_no")) == str(volume_no)
    ]
    return {
        "no": str(vol.get("no")), "name": str(vol.get("name") or ""),
        "detail_level": str(vol.get("detail_level") or "rough"),
        "core_conflict": str(vol.get("core_conflict") or ""),
        "goal": str(vol.get("goal") or ""),
        "climax": str(vol.get("climax") or ""),
        "hook_out": str(vol.get("hook_out") or ""),
        "ending_state": str(vol.get("ending_state") or ""),
        "units": sorted(skeletons, key=lambda u: str(u["id"])),
    }


def project_unit(planning: dict[str, Any], unit_id: str) -> dict[str, Any]:
    """单元层：本单元的全部详细字段（含 detail_level 与信息边界）。"""
    unit = next((u for u in planning.get("units") or []
                 if str(u.get("id")) == unit_id), None)
    if unit is None:
        raise PlanningInvalid(f"单元 {unit_id} 不存在")
    return {
        "id": str(unit.get("id")), "volume_no": str(unit.get("volume_no")),
        "name": str(unit.get("name") or ""),
        "detail_level": str(unit.get("detail_level") or "rough"),
        "chapters": unit.get("chapters") or [],
        "goal": str(unit.get("goal") or ""),
        "obstacle": str(unit.get("obstacle") or ""),
        "choices": [str(x) for x in (unit.get("choices") or [])],
        "outcome": str(unit.get("outcome") or ""),
        "can_reveal": [str(x) for x in (unit.get("can_reveal") or [])],
        "must_hold": [str(x) for x in (unit.get("must_hold") or [])],
        "story_lines": [str(x) for x in (unit.get("story_lines") or [])],
    }


def project_chapters(
    fm: dict[str, Any], unit_id: str, lo: int, hi: int,
) -> dict[str, Any]:
    """章纲层：该批次的七列行 + 单元 ID/范围 + 对应节奏小节。"""
    rows = [r for r in fm.get("chapter_rows") or []
            if (m := re.search(r"\d+", str(r.get("章", "")))) and lo <= int(m.group()) <= hi]
    rows.sort(key=lambda r: int(re.search(r"\d+", str(r["章"])).group()))
    return {
        "unit_id": unit_id, "from": lo, "to": hi,
        "rows": [{k: str(r.get(k, "")) for k in outline.CHAPTER_COLS} for r in rows],
        "rhythm": rhythm_batch(str(fm.get("_rhythm") or ""), unit_id, lo, hi),
    }


def rhythm_batch(rhythm: str, unit_id: str, lo: int, hi: int) -> str:
    """取固定 H2 节奏总谱下的 `### 章纲批次 <unit_id>:<from>-<to>` 小节正文。"""
    head = f"### 章纲批次 {unit_id}:{lo}-{hi}"
    m = re.search(rf"(?ms)^{re.escape(head)}\s*\n(.*?)(?=^###|^##\s|\Z)", rhythm)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# 就绪状态（规格08 §2.3）
# ---------------------------------------------------------------------------

def _status_of(content_digest: str, adoption: dict[str, Any] | None,
               parent_now: str) -> str:
    if adoption is None:
        return "unadopted"
    if str(adoption.get("content_digest") or "") != content_digest:
        return "unadopted"
    if str(adoption.get("parent_digest") or "") != parent_now:
        return "stale"
    return "ready"


def _layer(status: str, content_digest: str, adoption: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "status": status,
        "message": STATUS_MESSAGES[status],
        "content_digest": content_digest,
        "accepted_at": (adoption or {}).get("accepted_at"),
        "source": (adoption or {}).get("source"),
    }


def readiness(book: str) -> dict[str, Any]:
    """计算全书/卷/单元/章纲四层的就绪状态。legacy 书直接返回 legacy。"""
    try:
        info = read_planning(book)
    except PlanningInvalid as error:
        return {"mode": "invalid", "code": error.code,
                "message": f"无法判定：{error}", "layers": {}}
    if info["mode"] == "legacy":
        return {"mode": "legacy", "message": "旧模式（未启用内容规划）", "layers": {}}

    fm = read_outline_full(book)
    planning = info["planning"]
    adoptions = planning.get("adoptions") or {}

    global_digest = digest(project_global(fm, planning))
    layers: dict[str, Any] = {
        "global": _layer(_status_of(global_digest, adoptions.get("global"), ""),
                         global_digest, adoptions.get("global")),
        "volumes": [], "units": [], "chapters": [],
    }

    for vol in fm.get("volumes") or []:
        no = str(vol.get("no"))
        key = f"volume:{no}"
        try:
            d = digest(project_volume(fm, planning, no))
        except PlanningInvalid:
            layers["volumes"].append({"key": key, "status": "invalid",
                                      "message": STATUS_MESSAGES["invalid"]})
            continue
        layers["volumes"].append({"key": key, "no": no,
                                  "name": str(vol.get("name") or ""),
                                  **_layer(_status_of(d, adoptions.get(key), global_digest),
                                           d, adoptions.get(key))})

    for unit in planning.get("units") or []:
        uid = str(unit.get("id"))
        key = f"unit:{uid}"
        parent = _digest_or_none(lambda: digest(project_volume(
            fm, planning, str(unit.get("volume_no")))))
        try:
            d = digest(project_unit(planning, uid))
        except PlanningInvalid:
            layers["units"].append({"key": key, "status": "invalid",
                                    "message": STATUS_MESSAGES["invalid"]})
            continue
        status = "invalid" if parent is None else _status_of(d, adoptions.get(key), parent)
        layers["units"].append({"key": key, "id": uid,
                                "name": str(unit.get("name") or ""),
                                "chapters": unit.get("chapters") or [],
                                **_layer(status, d, adoptions.get(key))})

        # 章纲按「批次」记录（每批 ≤10 章，一个单元可分多批）：只枚举既有采纳
        # 记录对应的批次（规格08 §2.3 键 `chapters:<uid>:<from>-<to>`），
        # 未开始的批次不在这里出现，由 next_actions 提示下一批起点。
        for key, rec in sorted((adoptions or {}).items()):
            m = re.fullmatch(rf"chapters:{re.escape(uid)}:([0-9]+)-([0-9]+)", str(key))
            if not m:
                continue
            blo, bhi = int(m.group(1)), int(m.group(2))
            try:
                cd = digest(project_chapters(fm, uid, blo, bhi))
            except Exception:
                layers["chapters"].append({"key": key, "status": "invalid",
                                           "message": STATUS_MESSAGES["invalid"]})
                continue
            layers["chapters"].append({
                "key": key, "unit_id": uid, "from": blo, "to": bhi,
                **_layer(_status_of(cd, rec, d), cd, rec),
            })

    return {"mode": "rolling", "schema_version": SCHEMA_VERSION, "layers": layers}


def _digest_or_none(fn) -> str | None:
    try:
        return fn()
    except PlanningInvalid:
        return None


# ---------------------------------------------------------------------------
# 估算、保护集、装订
# ---------------------------------------------------------------------------

def estimates(book: str) -> dict[str, Any]:
    """估算总章数 = ceil(目标字数 / 单章区间中值)；作者覆盖值优先。

    目标或区间无效时返回 `source="unknown"` 且 `estimated_chapters=None`，
    不用默认值假装有效（规格08 §3.3）。
    """
    meta = books.read_meta(book)
    target = meta.get("target_words")
    rng = meta.get("chapter_word_range")
    override = None
    try:
        info = read_planning(book)
        if info["mode"] == "rolling":
            override = (info["planning"].get("global") or {}).get(
                "estimated_chapters_override")
    except PlanningInvalid:
        override = None

    valid_range = (isinstance(rng, list) and len(rng) == 2
                   and all(isinstance(x, int) and x > 0 for x in rng) and rng[0] <= rng[1])
    valid_target = isinstance(target, int) and target > 0
    if not (valid_range and valid_target):
        return {"target_words": target, "chapter_word_range": rng,
                "estimated_chapters": None, "source": "unknown",
                "message": "目标字数或单章字数区间无效，无法估算"}
    mid = (rng[0] + rng[1]) / 2
    auto = math.ceil(target / mid)
    if isinstance(override, int) and override > 0:
        return {"target_words": target, "chapter_word_range": rng,
                "estimated_chapters": override, "auto_chapters": auto,
                "source": "manual", "message": f"按作者设定 {override} 章（自动估算 {auto}）"}
    return {"target_words": target, "chapter_word_range": rng,
            "estimated_chapters": auto, "auto_chapters": auto,
            "source": "auto", "message": f"按目标字数自动估算 {auto} 章，仅供规划参考"}


def protected_numbers(book: str) -> list[int]:
    """保护集：已有正文（含未定稿）/记忆卡/已确认细纲的章号（规格08 §4.4）。

    只有 DB row 不算保护内容，因此这里只看文件。
    """
    out: set[int] = set()
    folder = chapters.chapters_dir(book)
    if folder.is_dir():
        for path in folder.glob("*.md"):
            m = re.fullmatch(r"([0-9]+)_(正文|记忆卡|细纲)(?:\.draft)?\.md", path.name)
            if not m:
                continue
            no = int(m.group(1))
            if m.group(2) != "细纲":
                out.add(no)
                continue
            try:
                if chapters.read_outline_contract(book, no).get("confirmed"):
                    out.add(no)
            except Exception:
                # 损坏但存在的正式内容同样纳入保护（规格08 §4.4）
                out.add(no)
    return sorted(out)


def tree(book: str) -> dict[str, Any]:
    """规划树：全书 → 卷 → 单元 → 章纲批次，附估算与实际已采纳行数。"""
    try:
        info = read_planning(book)
    except PlanningInvalid as error:
        return {"mode": "invalid", "code": error.code, "message": f"无法判定：{error}"}
    if info["mode"] == "legacy":
        return {"mode": "legacy", "message": "旧模式（未启用内容规划）"}

    fm = read_outline_full(book)
    planning = info["planning"]
    state = readiness(book)
    vols = []
    for vol in fm.get("volumes") or []:
        no = str(vol.get("no"))
        units = [
            {"id": str(u.get("id")), "name": str(u.get("name") or ""),
             "chapters": u.get("chapters") or [],
             "detail_level": str(u.get("detail_level") or "rough")}
            for u in planning.get("units") or [] if str(u.get("volume_no")) == no
        ]
        vols.append({"no": no, "name": str(vol.get("name") or ""),
                     "direction": str(vol.get("direction") or ""),
                     "detail_level": str(vol.get("detail_level") or "rough"),
                     "chapters": vol.get("chapters") or [],
                     "estimated_chapters": vol.get("estimated_chapters"),
                     "units": units})
    rows = fm.get("chapter_rows") or []
    row_nos = sorted({int(m.group()) for r in rows
                      if (m := re.search(r"\d+", str(r.get("章", ""))))})
    return {
        "mode": "rolling",
        "global": {"logline": str(fm.get("logline") or ""),
                   "ending_direction": str(fm.get("ending_direction") or ""),
                   "planning": planning.get("global") or {}},
        "volumes": vols,
        "chapter_rows": len(row_nos),
        "chapter_numbers": row_nos,
        "estimates": estimates(book),
        "readiness": state,
        "protected_numbers": protected_numbers(book),
    }


# ---------------------------------------------------------------------------
# 服务层（规格08 §3.1，阶段B第二步：版本指纹 / 门禁 / 影子合并 / 采纳）
#
# 边界不变：文件是真相、就绪是读模型不进 statemachine、缺 planning 即 legacy。
# 本层所有写入口都要求显式 If-Match（规格08 §3.1「不沿用旧端点兼容豁免」）。
# ---------------------------------------------------------------------------

from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from uuid import NAMESPACE_URL, uuid4, uuid5  # noqa: E402

# 规划稳定错误码 → HTTP（规格08 §3.2）
PLANNING_HTTP = {
    "PLANNING_INVALID": 422,
    "PLANNING_SCHEMA_UNSUPPORTED": 422,
    "PLANNING_RANGE_INVALID": 422,
    "PLANNING_PROMPT_INCOMPATIBLE": 422,
    "OUTLINE_INVALID": 422,
    "PLANNING_NOT_READY": 409,
    "PLANNING_PROTECTED": 409,
    "PLANNING_SCOPE_REQUIRED": 409,
    "PLANNING_PREVIEW_CHANGED": 409,
    "PLANNING_INPUT_CHANGED": 409,
    "PLANNING_NOT_FOUND": 404,
    "PLANNING_VERSION_REQUIRED": 428,
}

GLOBAL_FIELDS = ("reader_promise", "selling_points", "mainline",
                 "stage_boundaries", "reserved_reveals",
                 "estimated_chapters_override")
VOLUME_DETAIL_FIELDS = ("core_conflict", "goal", "climax", "hook_out", "ending_state")
UNIT_DETAIL_FIELDS = ("goal", "obstacle", "choices", "outcome",
                      "can_reveal", "must_hold", "story_lines")
SCOPES = ("global", "volume", "unit", "chapters")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 规划版本（规格08 §3.1：文件原始字节与存在性的组合指纹）
# ---------------------------------------------------------------------------

def version_entries(book: str) -> list[tuple[str, str, bytes | None]]:
    """参与规划版本的文件集合：正式档案 + 章节文件 + 生效 G5/G6 模板。

    摘要按固定源标识与相对文件名排序计算；新增/删除文件同样纳入（missing 条目）。
    DB、运行日志、草稿/备份/候选、前端缓存一律不进版本（规格08 §3.1）。
    """
    from . import prompts as _prompts
    from .paths import book_dir as _book_dir

    bdir = _book_dir(book)

    def _read(name: str) -> bytes | None:
        path = bdir / name
        return path.read_bytes() if path.is_file() else None

    entries: list[tuple[str, str, bytes | None]] = [
        (sid, name, _read(name)) for sid, name in (
            ("outline", "OUTLINE.md"), ("meta", "meta.md"),
            ("config", "config.json"), ("bible", "STORY_BIBLE.md"),
            ("plot_points", "PLOT_POINTS.md"), ("locations", "LOCATIONS.md"),
            ("timeline", "TIMELINE.md"),
        )
    ]
    for sub in ("characters", "chapters"):
        folder = bdir / sub
        if folder.is_dir():
            for path in sorted(folder.glob("*.md")):
                entries.append((sub, f"{sub}/{path.name}",
                                path.read_bytes() if path.is_file() else None))
    for pid in ("G5", "G6"):
        try:
            p = _prompts.get(pid, book)
        except Exception:
            continue  # 模板读不出不阻塞读模型；写入口的 gate 再报
        entries.append((f"prompt:{pid}", f"prompt/{pid}@{p.source}",
                        p.digest.encode("utf-8")))
    entries.sort(key=lambda e: (e[0], e[1]))
    return entries


def version_etag(book: str) -> str:
    digest = hashlib.sha256()
    for sid, name, payload in version_entries(book):
        digest.update(f"{sid}:{name}".encode("utf-8"))
        digest.update(b"\0")
        if payload is None:
            digest.update(b"missing\0")
        else:
            digest.update(b"file\0")
            digest.update(hashlib.sha256(payload).digest())
            digest.update(b"\0")
    return f'"sha256-{digest.hexdigest()}"'


def version_source_files(book: str) -> list[str]:
    """实际参与版本的源文件清单（排障用；不展示设置密钥）。"""
    return [f"{sid}:{name}" for sid, name, _ in version_entries(book)]


# ---------------------------------------------------------------------------
# 读模型（GET /planning）
# ---------------------------------------------------------------------------

def unassigned_numbers(book: str) -> list[int]:
    """rolling 书里不属于任何单元范围的正式章行号（规格08 §4.3）。"""
    try:
        info = read_planning(book)
        if info["mode"] != "rolling":
            return []
        fm = read_outline_full(book)
    except PlanningInvalid:
        return []
    covered: set[int] = set()
    for unit in info["planning"].get("units") or []:
        rng = unit.get("chapters") or []
        if len(rng) == 2 and all(isinstance(x, int) for x in rng):
            covered.update(range(rng[0], rng[1] + 1))
    rows = [int(m.group()) for r in fm.get("chapter_rows") or []
            if (m := re.search(r"\d+", str(r.get("章", ""))))]
    return sorted(set(rows) - covered)


def _layer_ready(state: dict[str, Any], key: str) -> bool:
    """按层键查就绪状态；global 是字典键，卷/单元/章纲在各自列表里。"""
    hit = state.get(key)
    if isinstance(hit, dict):
        return hit.get("status") == "ready"
    for group in ("volumes", "units", "chapters"):
        for lay in state.get(group) or []:
            if isinstance(lay, dict) and lay.get("key") == key:
                return lay.get("status") == "ready"
    return False


def generation_gate(book: str, scope: str, target: dict[str, Any]) -> dict[str, Any]:
    """下层生成/采纳前检查上游就绪（规格08 §6.3 P-T06）。"""
    try:
        info = read_planning(book)
    except PlanningInvalid as error:
        return {"allowed": False, "code": error.code, "message": f"无法判定：{error}"}
    if info["mode"] != "rolling":
        return {"allowed": False, "code": "PLANNING_SCOPE_REQUIRED",
                "message": "旧模式书没有规划层，请先在规划页显式启用内容规划"}
    state = readiness(book)["layers"]
    if scope == "global":
        return {"allowed": True, "code": None, "message": None}
    if not _layer_ready(state, "global"):
        return {"allowed": False, "code": "PLANNING_NOT_READY",
                "message": "全书粗纲尚未采纳，先在全书层生成并人工采纳"}
    if scope == "volume":
        return {"allowed": True, "code": None, "message": None}
    no = str(target.get("volume_no"))
    if not _layer_ready(state, f"volume:{no}"):
        return {"allowed": False, "code": "PLANNING_NOT_READY",
                "message": f"卷 {no} 的卷纲尚未采纳，先展开当前卷并人工采纳"}
    if scope == "unit":
        return {"allowed": True, "code": None, "message": None}
    uid = str(target.get("unit_id") or "")
    if not _layer_ready(state, f"unit:{uid}"):
        return {"allowed": False, "code": "PLANNING_NOT_READY",
                "message": f"单元 {uid} 详情尚未采纳，先细化该单元并人工采纳"}
    return {"allowed": True, "code": None, "message": None}


def next_actions(book: str) -> list[dict[str, str]]:
    """按就绪状态给出下一步（规格08 §3.1 next_actions）。

    顺序遵循「不自动越过前卷」：逐卷推进——当前卷的卷纲 → 该卷全部单元
    → 该卷各单元的下一章纲批次，全部就绪后才建议下一卷的卷纲。
    """
    try:
        state = readiness(book)
    except Exception:
        return []
    if state["mode"] != "rolling":
        return [{"action": "enable", "message": "旧模式书：可显式启用内容规划（不改旧稿）"}]
    layers = state["layers"]
    if layers.get("global", {}).get("status") != "ready":
        return [{"action": "generate", "scope": "global",
                 "message": "全书层：生成粗纲（主线/阶段边界/各卷方向），人工采纳"}]
    try:
        info = read_planning(book)
        fm = read_outline_full(book)
    except PlanningInvalid:
        return []
    covered: set[int] = set()
    for row in fm.get("chapter_rows") or []:
        if (m := re.search(r"\d+", str(row.get("章", "")))):
            covered.add(int(m.group()))
    units = list(info["planning"].get("units") or [])
    vol_nos = [str(vol.get("no")) for vol in fm.get("volumes") or []]
    matched: set[int] = set()

    def _unit_hint(unit: dict) -> dict[str, str]:
        return {"action": "generate", "scope": "unit",
                "target": {"volume_no": None, "unit_id": unit.get("id"), "chapters": None},
                "message": f"单元 {unit.get('name')}：细化目标/阻力/选择/结果与信息边界"}

    def _batch_hint(unit: dict) -> dict[str, str] | None:
        rng = unit.get("chapters") or []
        if not (len(rng) == 2 and all(isinstance(x, int) for x in rng)):
            return None
        for no in range(rng[0], rng[1] + 1):
            if no not in covered:
                hi = min(no + 9, rng[1])
                return {"action": "generate", "scope": "chapters",
                        "target": {"volume_no": None, "unit_id": unit.get("id"),
                                   "chapters": [no, hi]},
                        "message": f"章纲批次 {no}-{hi}：生成七列章纲（单元 {unit.get('name')}）"}
        return None

    for no in vol_nos:
        if not _layer_ready(layers, f"volume:{no}"):
            return [{"action": "generate", "scope": "volume",
                     "target": {"volume_no": no, "unit_id": None, "chapters": None},
                     "message": f"卷 {no}：生成卷纲与单元骨架，人工采纳"}]
        for idx, unit in enumerate(units):
            if str(unit.get("volume_no")) != no:
                continue
            matched.add(idx)
            if not _layer_ready(layers, f"unit:{str(unit.get('id'))}"):
                return [_unit_hint(unit)]
        for idx, unit in enumerate(units):
            if str(unit.get("volume_no")) != no:
                continue
            hint = _batch_hint(unit)
            if hint:
                return [hint]
    # 不属于任何已声明卷的单元（异常数据）：兜底提示，避免被静默跳过
    for idx, unit in enumerate(units):
        if idx in matched:
            continue
        if not _layer_ready(layers, f"unit:{str(unit.get('id'))}"):
            return [_unit_hint(unit)]
        hint = _batch_hint(unit)
        if hint:
            return [hint]
    return [{"action": "workbench", "message": "规划就绪：可到工作台生成单章细纲"}]


def read_model(book: str) -> dict[str, Any]:
    """GET /planning 的完整读模型（只读，不写任何文件）。"""
    warnings: list[str] = []
    try:
        info = read_planning(book)
    except PlanningInvalid as error:
        return {"mode": "invalid", "schema_version": None, "tree": None,
                "readiness": {"mode": "invalid", "code": error.code,
                              "message": f"无法判定：{error}", "layers": {}},
                "chapter_numbers": [], "protected_numbers": [],
                "unassigned_numbers": [], "estimates": estimates(book),
                "next_actions": [], "warnings": [f"规划元数据无法判定：{error}"],
                "generation_gate": {"allowed": False, "code": error.code,
                                    "message": "修复规划元数据后才能继续"}}
    mode = info["mode"]
    tr = tree(book)
    state = readiness(book)
    if mode == "rolling":
        for lay in (state.get("layers", {}).get("volumes") or []):
            if lay.get("status") == "invalid":
                warnings.append(f"卷 {lay.get('key')} 结构损坏，需修复")
        unr = unassigned_numbers(book)
        if unr:
            warnings.append(f"章行 {unr} 未映射到任何单元，请人工映射或另开新卷")
    return {
        "mode": mode,
        "schema_version": info["schema_version"],
        "tree": tr,
        "readiness": state,
        "chapter_numbers": tr.get("chapter_numbers") or [],
        "protected_numbers": tr.get("protected_numbers") or [],
        "unassigned_numbers": unassigned_numbers(book) if mode == "rolling" else [],
        "estimates": estimates(book),
        "next_actions": next_actions(book),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# 启用（POST /planning/enable，规格08 §4.2 幂等）
# ---------------------------------------------------------------------------

def enable(book: str) -> dict[str, Any]:
    """只加空规划元数据：schema_version/enabled/global/units/adoptions。

    保留原 OUTLINE 全部字段、章节表与节奏；不改任何旧稿；重复启用幂等返回。
    """
    try:
        fm = read_outline_full(book)
    except FileNotFoundError as error:
        raise PlanningInvalid("OUTLINE.md 不存在，无法启用内容规划", 
                              code="OUTLINE_INVALID") from error
    except Exception as error:
        raise PlanningInvalid(f"OUTLINE.md 读取失败，无法启用：{error}",
                              code="OUTLINE_INVALID") from error
    raw = fm.get("planning")
    if isinstance(raw, dict):
        version = raw.get("schema_version", SCHEMA_VERSION)
        if version != SCHEMA_VERSION:
            raise PlanningInvalid(
                f"不支持的 planning.schema_version={version!r}",
                code="PLANNING_SCHEMA_UNSUPPORTED")
        if raw.get("enabled") is True:
            return read_model(book)  # 幂等：不清采纳记录
        raw = {k: v for k, v in raw.items() if k != "enabled"}
        planning = {"schema_version": SCHEMA_VERSION, "enabled": True,
                    "global": raw.get("global") or {}, "units": raw.get("units") or [],
                    "adoptions": raw.get("adoptions") or {}}
    else:
        planning = {"schema_version": SCHEMA_VERSION, "enabled": True,
                    "global": {}, "units": [], "adoptions": {}}
    fm = {k: v for k, v in fm.items() if not k.startswith("_")}
    fm.pop("chapter_rows", None)
    fm["planning"] = planning
    outline.write_outline(book, fm, rows=read_outline_full(book)["chapter_rows"],
                          rhythm=str(read_outline_full(book).get("_rhythm") or ""))
    return read_model(book)


# ---------------------------------------------------------------------------
# 候选解析与校验（规格08 §2.4：严格 JSON 包络，越 scope 整次拒绝）
# ---------------------------------------------------------------------------

def _require_str(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise PlanningInvalid(f"{field} 必须是字符串", code="PLANNING_INVALID")
    return value.strip()


def _require_str_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise PlanningInvalid(f"{field} 必须是字符串列表", code="PLANNING_INVALID")
    return [x.strip() for x in value]


def normalize_target(scope: str, target: Any) -> dict[str, Any]:
    """`target` 固定结构 {volume_no, unit_id, chapters}（规格08 §3.1）。"""
    if not isinstance(target, dict):
        raise PlanningInvalid("target 必须是 {volume_no, unit_id, chapters} 结构",
                              code="PLANNING_RANGE_INVALID")
    if scope not in SCOPES:
        raise PlanningInvalid(f"不支持的作用域 {scope!r}（legacy 请走既有 G5 流程）",
                              code="PLANNING_INVALID")
    volume_no = target.get("volume_no")
    unit_id = target.get("unit_id")
    chapters = target.get("chapters")
    if scope == "global":
        if volume_no is not None or unit_id is not None or chapters is not None:
            raise PlanningInvalid("global 作用域的 target 必须全空",
                                  code="PLANNING_RANGE_INVALID")
    elif scope == "volume":
        if not isinstance(volume_no, int) or isinstance(volume_no, bool) or volume_no <= 0:
            raise PlanningInvalid("volume 作用域必须提供正整数卷号",
                                  code="PLANNING_RANGE_INVALID")
        if unit_id is not None or chapters is not None:
            raise PlanningInvalid("volume 作用域不接受 unit_id/chapters",
                                  code="PLANNING_RANGE_INVALID")
    elif scope == "unit":
        if not isinstance(volume_no, int) or volume_no <= 0:
            raise PlanningInvalid("unit 作用域必须提供卷号", code="PLANNING_RANGE_INVALID")
        if not re.fullmatch(r"U-[0-9a-f]{32}", str(unit_id or "")):
            raise PlanningInvalid("unit 作用域必须提供合法单元 ID", code="PLANNING_RANGE_INVALID")
        if chapters is not None:
            raise PlanningInvalid("unit 作用域不接受 chapters", code="PLANNING_RANGE_INVALID")
    else:  # chapters
        if not isinstance(volume_no, int) or volume_no <= 0:
            raise PlanningInvalid("chapters 作用域必须提供卷号", code="PLANNING_RANGE_INVALID")
        if not re.fullmatch(r"U-[0-9a-f]{32}", str(unit_id or "")):
            raise PlanningInvalid("chapters 作用域必须提供单元 ID", code="PLANNING_RANGE_INVALID")
        if not (isinstance(chapters, list) and len(chapters) == 2
                and all(isinstance(x, int) and not isinstance(x, bool) and x > 0
                        for x in chapters) and chapters[0] <= chapters[1]):
            raise PlanningInvalid("chapters 作用域必须提供 [起,止] 正整数章号区间",
                                  code="PLANNING_RANGE_INVALID")
    return {"volume_no": volume_no, "unit_id": unit_id, "chapters": chapters}


def parse_candidate(content: str, scope: str) -> dict[str, Any]:
    """解析模型输出的严格 JSON 包络 `{scope,target,data}`。

    只允许对应作用域字段；结构不合法或越 scope 整次拒绝（规格08 §2.4、§5）。
    """
    try:
        envelope = json.loads(content)
    except json.JSONDecodeError as error:
        raise PlanningInvalid(f"候选不是合法 JSON：{error}", code="PLANNING_INVALID")
    if not isinstance(envelope, dict):
        raise PlanningInvalid("候选必须是 JSON 对象", code="PLANNING_INVALID")
    unknown_top = set(envelope) - {"scope", "target", "data"}
    if unknown_top:
        raise PlanningInvalid(f"包络只允许 scope/target/data，多余字段：{sorted(unknown_top)}",
                              code="PLANNING_INVALID")
    if envelope.get("scope") != scope:
        raise PlanningInvalid(f"包络 scope={envelope.get('scope')!r} 与请求作用域 {scope!r} 不一致",
                              code="PLANNING_INVALID")
    target = normalize_target(scope, envelope.get("target"))
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise PlanningInvalid("包络 data 必须是 JSON 对象", code="PLANNING_INVALID")
    _validate_scope_data(scope, data)
    return {"scope": scope, "target": target, "data": data}


def _validate_scope_data(scope: str, data: dict[str, Any]) -> None:
    if scope == "global":
        allowed = {"logline", "ending_direction", "global", "rough_volumes"}
        if set(data) - allowed:
            raise PlanningInvalid(f"global 作用域只允许 {sorted(allowed)}",
                                  code="PLANNING_INVALID")
        _require_str(data.get("logline"), "data.logline")
        _require_str(data.get("ending_direction"), "data.ending_direction")
        g = data.get("global")
        if not isinstance(g, dict):
            raise PlanningInvalid("data.global 必须是对象", code="PLANNING_INVALID")
        for field in ("selling_points", "stage_boundaries", "reserved_reveals"):
            if field in g:
                _require_str_list(g[field], f"global.{field}")
        for field in ("reader_promise", "mainline"):
            if field in g:
                _require_str(g[field], f"global.{field}")
        override = g.get("estimated_chapters_override")
        if override is not None and (not isinstance(override, int)
                                     or isinstance(override, bool) or override <= 0):
            raise PlanningInvalid("estimated_chapters_override 须为正整数或 null",
                                  code="PLANNING_RANGE_INVALID")
        vols = data.get("rough_volumes")
        if not isinstance(vols, list):
            raise PlanningInvalid("rough_volumes 必须是列表", code="PLANNING_INVALID")
        seen: set[int] = set()
        for v in vols:
            if not isinstance(v, dict):
                raise PlanningInvalid("rough_volumes 每项必须是对象", code="PLANNING_INVALID")
            no = v.get("no")
            if not isinstance(no, int) or no <= 0 or no in seen:
                raise PlanningInvalid("rough_volumes 的卷号须为不重复正整数",
                                      code="PLANNING_RANGE_INVALID")
            seen.add(no)
            for field in ("name", "direction"):
                if field in v:
                    _require_str(v[field], f"rough_volumes.{field}")
            est = v.get("estimated_chapters")
            if est is not None and (not isinstance(est, int) or est <= 0):
                raise PlanningInvalid("estimated_chapters 须为正整数",
                                      code="PLANNING_RANGE_INVALID")
    elif scope == "volume":
        allowed = {"chapter_count", "volume_detail", "unit_skeletons"}
        if set(data) - allowed:
            raise PlanningInvalid(f"volume 作用域只允许 {sorted(allowed)}",
                                  code="PLANNING_INVALID")
        count = data.get("chapter_count")
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise PlanningInvalid("chapter_count 须为正整数", code="PLANNING_RANGE_INVALID")
        detail = data.get("volume_detail")
        if not isinstance(detail, dict):
            raise PlanningInvalid("volume_detail 必须是对象", code="PLANNING_INVALID")
        for field in VOLUME_DETAIL_FIELDS:
            if field in detail:
                _require_str(detail[field], f"volume_detail.{field}")
        skeletons = data.get("unit_skeletons")
        if not isinstance(skeletons, list) or not skeletons:
            raise PlanningInvalid("unit_skeletons 必须是非空列表", code="PLANNING_INVALID")
        for sk in skeletons:
            if not isinstance(sk, dict):
                raise PlanningInvalid("unit_skeletons 每项必须是对象", code="PLANNING_INVALID")
            _require_str(sk.get("name"), "unit_skeletons.name")
            for field in ("goal", "obstacle"):
                if field in sk:
                    _require_str(sk[field], f"unit_skeletons.{field}")
    elif scope == "unit":
        if set(data) != {"unit_detail"}:
            raise PlanningInvalid("unit 作用域只允许 unit_detail", code="PLANNING_INVALID")
        detail = data.get("unit_detail")
        if not isinstance(detail, dict):
            raise PlanningInvalid("unit_detail 必须是对象", code="PLANNING_INVALID")
        for field in ("goal", "obstacle", "outcome"):
            _require_str(detail.get(field), f"unit_detail.{field}")
        for field in ("choices", "can_reveal", "must_hold", "story_lines"):
            if field in detail:
                _require_str_list(detail[field], f"unit_detail.{field}")
    else:  # chapters
        allowed = {"chapter_rows", "rhythm_note"}
        if set(data) - allowed:
            raise PlanningInvalid(f"chapters 作用域只允许 {sorted(allowed)}",
                                  code="PLANNING_INVALID")
        rows = data.get("chapter_rows")
        if not isinstance(rows, list) or not rows:
            raise PlanningInvalid("chapter_rows 必须是非空列表", code="PLANNING_INVALID")
        seen_rows: set[int] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise PlanningInvalid("chapter_rows 每项必须是对象", code="PLANNING_INVALID")
            m = re.search(r"\d+", str(row.get("章", "")))
            if not m:
                raise PlanningInvalid("chapter_rows 的「章」列必须有章号", code="PLANNING_RANGE_INVALID")
            no = int(m.group())
            if no in seen_rows:
                raise PlanningInvalid(f"章号 {no} 重复", code="PLANNING_RANGE_INVALID")
            seen_rows.add(no)
            for col in outline.CHAPTER_COLS:
                if col in row:
                    _require_str(row[col], f"chapter_rows.{col}")
        if "rhythm_note" in data:
            _require_str(data["rhythm_note"], "rhythm_note")


# ---------------------------------------------------------------------------
# 影子合并（规格08 §2.4：只改目标作用域，其余一律保留）
# ---------------------------------------------------------------------------

def _load_state(book: str) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    """读当前 fm + planning + 章节行（深拷贝出可变工作副本）。

    先 read_planning 再读全文：OUTLINE.md 损坏时先拿到稳定 PlanningInvalid，
    不让 FrontmatterError 裸奔成 500。
    """
    info = read_planning(book)
    if info["mode"] != "rolling":
        raise PlanningInvalid("旧模式书没有规划层，请先显式启用内容规划",
                              code="PLANNING_SCOPE_REQUIRED")
    fm = read_outline_full(book)
    planning = json.loads(json.dumps(info["planning"]))  # 深拷贝，绝不改原引用
    rows = [dict(r) for r in fm.get("chapter_rows") or []]
    return dict(fm), planning, rows


def _max_reserved_number(fm: dict[str, Any], rows: list[dict[str, str]]) -> int:
    """有效章节表行 + 详细卷预留范围的最大章号（规格08 §2.2 约束5）。"""
    numbers = [int(m.group()) for r in rows
               if (m := re.search(r"\d+", str(r.get("章", ""))))]
    for vol in fm.get("volumes") or []:
        if str(vol.get("detail_level") or "rough") == "expanded":
            rng = vol.get("chapters") or []
            if len(rng) == 2:
                numbers.append(int(rng[1]))
    return max(numbers, default=0)


def planning_unit_id(book: str, volume_no: Any, name: str, lo: int, hi: int) -> str:
    """程序分配的单元 ID（规格08 §2.2 约束4 / §3.1）。

    用 uuid5 从「书 + 卷 + 单元名 + 范围」**确定性**派生：预览分配的 ID 在重预览
    与采纳时必然复用，不会因为服务端重新计算而变号（采纳后就固化在 OUTLINE.md，
    之后不随标题/排序改变）。模型给的任意 ID 一律不采信。
    """
    key = f"{book}|volume:{volume_no}|unit:{name}|chapters:{lo}-{hi}"
    return f"U-{uuid5(NAMESPACE_URL, key).hex}"


def merge_candidate(book: str, scope: str, target: dict[str, Any],
                    data: dict[str, Any], *, projection: dict[str, Any] | None = None) -> dict[str, Any]:
    """把校验过的候选影子合并到当前正式数据上（纯计算，不写盘）。

    返回 {new_fm, rows, rhythm, new_planning, assigned, impact}。
    新卷号/单元ID/章号在这里由**程序**分配并放进规范化候选（规格08 §3.1）。
    """
    if projection is None:
        fm, planning, rows = _load_state(book)
    else:
        fm = json.loads(json.dumps(projection["outline"]))
        planning = fm["planning"]
        rows = [dict(row) for row in fm.get("chapter_rows") or []]
    impact: dict[str, Any] = {"scope": scope, "target": target}

    if scope == "global":
        fm["logline"] = _require_str(data["logline"], "data.logline")
        fm["ending_direction"] = _require_str(data["ending_direction"], "data.ending_direction")
        g = data.get("global") or {}
        planning["global"] = {k: g.get(k) for k in GLOBAL_FIELDS if k in g}
        volumes = [dict(v) for v in fm.get("volumes") or []]
        existing_nos = {int(v["no"]) for v in volumes if str(v.get("no", "")).isdigit()}
        next_no = max(existing_nos, default=0) + 1
        volumes_added = []
        for rv in data.get("rough_volumes") or []:
            no = int(rv["no"])
            hit = next((v for v in volumes
                        if str(v.get("no", "")).isdigit() and int(v["no"]) == no), None)
            if hit is None:
                hit = {"no": no, "name": "", "chapters": [], "detail_level": "rough"}
                volumes.append(hit)
                volumes_added.append(no)
                if no >= next_no:
                    next_no = no + 1
            # 只改粗方向投影；已展开卷细节/单元/章纲一律保留（不降级 rough）
            hit["name"] = str(rv.get("name") or hit.get("name") or "")
            hit["direction"] = str(rv.get("direction") or "")
            if rv.get("estimated_chapters"):
                hit["estimated_chapters"] = int(rv["estimated_chapters"])
        fm["volumes"] = volumes
        impact["volumes_added"] = volumes_added
        impact["volumes_touched"] = [int(rv["no"]) for rv in data.get("rough_volumes") or []]

    elif scope == "volume":
        no = target["volume_no"]
        vol = next((v for v in fm.get("volumes") or []
                    if str(v.get("no", "")) == str(no)), None)
        if vol is None:
            raise PlanningInvalid(f"卷 {no} 不存在，先用全书层生成各卷粗方向",
                                  code="PLANNING_NOT_FOUND")
        # 已有已采纳/受保护内容的卷不允许整体重排骨架（规格08 §4.5）
        protected = set(protected_numbers(book))
        old_units = [u for u in planning.get("units") or []
                     if str(u.get("volume_no")) == str(no)]
        touched: set[int] = set()
        for u in old_units:
            rng = u.get("chapters") or []
            if len(rng) == 2:
                touched.update(range(int(rng[0]), int(rng[1]) + 1))
        clash = sorted(protected & touched)
        if clash:
            raise PlanningInvalid(
                f"卷 {no} 的既有单元范围含受保护章 {clash}，不能整体替换骨架；"
                "请改用单元/章纲作用域增量调整", code="PLANNING_PROTECTED")
        count = int(data["chapter_count"])
        start = _max_reserved_number(fm, rows) + 1
        end = start + count - 1
        detail = data.get("volume_detail") or {}
        vol.update({k: str(detail.get(k, "") or "") for k in VOLUME_DETAIL_FIELDS})
        vol["detail_level"] = "expanded"
        vol["chapters"] = [start, end]
        # 骨架范围必须完整覆盖整卷且不重叠不留洞（规格08 §2.2 约束5）
        skeletons = data.get("unit_skeletons") or []
        planning["units"] = [u for u in planning.get("units") or []
                             if str(u.get("volume_no")) != str(no)]
        assigned_ids = []
        cursor = start
        for sk in skeletons:
            rng = sk.get("chapters")
            if rng is not None:
                if not (isinstance(rng, list) and len(rng) == 2
                        and all(isinstance(x, int) and x > 0 for x in rng)):
                    raise PlanningInvalid("unit_skeletons.chapters 须为 [a,b] 正整数",
                                          code="PLANNING_RANGE_INVALID")
                lo, hi = int(rng[0]), int(rng[1])
                if lo != cursor or hi < lo:
                    raise PlanningInvalid(
                        f"单元骨架范围 [{lo},{hi}] 必须从 {cursor} 起连续无重叠",
                        code="PLANNING_RANGE_INVALID")
            else:
                lo, hi = cursor, cursor + int(sk.get("chapter_count") or 1) - 1
            if hi > end:
                raise PlanningInvalid(f"单元骨架 [{lo},{hi}] 越出本卷范围 [{start},{end}]",
                                      code="PLANNING_RANGE_INVALID")
            uid = planning_unit_id(book, no, str(sk.get("name") or ""), lo, hi)
            planning["units"].append({
                "id": uid, "volume_no": no, "name": str(sk.get("name") or ""),
                "detail_level": "rough", "chapters": [lo, hi],
                "goal": str(sk.get("goal") or ""), "obstacle": str(sk.get("obstacle") or ""),
            })
            assigned_ids.append(uid)
            cursor = hi + 1
        if cursor != end + 1:
            raise PlanningInvalid(
                f"单元骨架只覆盖到第 {cursor - 1} 章，必须完整覆盖本卷 [{start},{end}]",
                code="PLANNING_RANGE_INVALID")
        impact["volume_no"] = no
        impact["volume_range"] = [start, end]
        impact["units_added"] = assigned_ids
        target = {**target, "volume_no": no}

    elif scope == "unit":
        uid = target["unit_id"]
        unit = next((u for u in planning.get("units") or []
                     if str(u.get("id")) == uid), None)
        if unit is None:
            raise PlanningInvalid(f"单元 {uid} 不存在，先用卷层生成单元骨架",
                                  code="PLANNING_NOT_FOUND")
        detail = data.get("unit_detail") or {}
        for field in UNIT_DETAIL_FIELDS:
            if field in detail:
                unit[field] = detail[field]
        unit["detail_level"] = "expanded"
        # 单元层不改 ID/名称/范围（规格08 §2.4）
        impact["unit_id"] = uid

    else:  # chapters
        uid = target["unit_id"]
        unit = next((u for u in planning.get("units") or []
                     if str(u.get("id")) == uid), None)
        if unit is None:
            raise PlanningInvalid(f"单元 {uid} 不存在", code="PLANNING_NOT_FOUND")
        if str(unit.get("detail_level") or "rough") != "expanded":
            raise PlanningInvalid(f"单元 {uid} 尚未细化（rough），先采纳单元详情",
                                  code="PLANNING_NOT_READY")
        urange = unit.get("chapters") or []
        lo, hi = target["chapters"]
        if not (len(urange) == 2 and urange[0] <= lo <= hi <= urange[1]):
            raise PlanningInvalid(f"批次 [{lo},{hi}] 不在单元范围 {urange} 内",
                                  code="PLANNING_RANGE_INVALID")
        if hi - lo + 1 > 10:
            raise PlanningInvalid("单批章纲不超过 10 章（规格08 §2.4）",
                                  code="PLANNING_RANGE_INVALID")
        new_rows = data.get("chapter_rows") or []
        row_nos = sorted(int(re.search(r"\d+", str(r.get("章", ""))).group())
                         for r in new_rows)
        if row_nos != list(range(lo, hi + 1)):
            raise PlanningInvalid(
                f"批次行必须完整覆盖 [{lo},{hi}] 且不越范围，当前行号 {row_nos}",
                code="PLANNING_RANGE_INVALID")
        # 保护集：批次内受保护章的行内容不允许被改动（规格08 §4.5）
        protected = set(protected_numbers(book))
        old_map = {int(re.search(r"\d+", str(r.get("章", ""))).group()): r for r in rows}
        for no in row_nos:
            new_row = {c: str(r.get(c, "") or "").strip()
                       for r in new_rows
                       if int(re.search(r"\d+", str(r.get("章", ""))).group()) == no
                       for c in outline.CHAPTER_COLS}
            old_row = old_map.get(no)
            if no in protected:
                old_norm = {c: str((old_row or {}).get(c, "") or "").strip()
                            for c in outline.CHAPTER_COLS}
                if old_row is None or old_norm != new_row:
                    raise PlanningInvalid(
                        f"第 {no} 章已写正文/记忆卡/已确认细纲（受保护），新规划不得改其章行",
                        code="PLANNING_PROTECTED")
        added = [no for no in row_nos if no not in old_map]
        changed = [no for no in row_nos if no in old_map and {
            c: str(old_map[no].get(c, "") or "").strip() for c in outline.CHAPTER_COLS} != {
            c: str(next(r for r in new_rows
                        if int(re.search(r"\d+", str(r.get("章", ""))).group()) == no)
                   .get(c, "") or "").strip() for c in outline.CHAPTER_COLS}]
        rows = [r for r in rows
                if not (lo <= int(re.search(r"\d+", str(r.get("章", ""))).group() or 0) <= hi)]
        for r in new_rows:
            rows.append({c: str(r.get(c, "") or "") for c in outline.CHAPTER_COLS})
        rows.sort(key=lambda r: int(re.search(r"\d+", str(r.get("章", "0"))).group()
                                    if re.search(r"\d+", str(r.get("章", "0"))) else 0))
        impact["added_numbers"] = added
        impact["changed_numbers"] = changed
        impact["batch"] = [lo, hi]

    # rhythm：chapters 作用域更新固定批次小节，其余小节与手工正文保留
    rhythm = str((fm if projection is not None else read_outline_full(book)).get("_rhythm") or "")
    if scope == "chapters" and data.get("rhythm_note") is not None:
        lo, hi = target["chapters"]
        head = f"### 章纲批次 {target['unit_id']}:{lo}-{hi}"
        section = f"{head}\n{str(data['rhythm_note']).strip()}"
        pattern = rf"(?ms)^{re.escape(head)}\s*\n.*?(?=^###|^##\s|\Z)"
        if re.search(pattern, rhythm):
            rhythm = re.sub(pattern, lambda _m: section + "\n\n", rhythm)
        else:
            rhythm = (rhythm.rstrip() + "\n\n" + section + "\n") if rhythm.strip() else section + "\n"

    new_fm = {k: v for k, v in fm.items() if not k.startswith("_") and k != "chapter_rows"}
    new_fm["planning"] = planning
    return {"new_fm": new_fm, "rows": rows, "rhythm": rhythm,
            "new_planning": planning, "target": target, "impact": impact}


def _layer_projection(book_scope: str, merged: dict[str, Any],
                      target: dict[str, Any]) -> tuple[str, str]:
    """算目标层与父层的内容指纹（规格08 §2.3 投影集中定义）。"""
    fm = dict(merged["new_fm"])
    fm["chapter_rows"] = merged["rows"]
    fm["_rhythm"] = merged["rhythm"]
    planning = merged["new_planning"]
    if book_scope == "global":
        return digest(project_global(fm, planning)), ""
    if book_scope == "volume":
        no = str(target["volume_no"])
        return (digest(project_volume(fm, planning, no)),
                digest(project_global(fm, planning)))
    if book_scope == "unit":
        uid = str(target["unit_id"])
        return (digest(project_unit(planning, uid)),
                digest(project_volume(fm, planning, str(target["volume_no"]))))
    lo, hi = target["chapters"]
    uid = str(target["unit_id"])
    return (digest(_project_batch(fm, planning, uid, lo, hi)),
            digest(project_unit(planning, uid)))


def _project_batch(fm: dict[str, Any], planning: dict[str, Any],
                   unit_id: str, lo: int, hi: int) -> dict[str, Any]:
    """章纲批次投影：该批次的七列行 + 单元/范围 + 对应节奏小节。"""
    rows = [r for r in fm.get("chapter_rows") or []
            if (m := re.search(r"\d+", str(r.get("章", "")))) and lo <= int(m.group()) <= hi]
    rows.sort(key=lambda r: int(re.search(r"\d+", str(r.get("章"))).group()))
    return {
        "unit_id": unit_id, "from": lo, "to": hi,
        "rows": [{k: str(r.get(k, "")) for k in outline.CHAPTER_COLS} for r in rows],
        "rhythm": rhythm_batch(str(fm.get("_rhythm") or ""), unit_id, lo, hi),
    }


# ---------------------------------------------------------------------------
# 预览与采纳（规格08 §3.1：纯计算预览 + 写后核对的采纳）
# ---------------------------------------------------------------------------

def _scope_key(scope: str, target: dict[str, Any]) -> str:
    if scope == "global":
        return "global"
    if scope == "volume":
        return f"volume:{target['volume_no']}"
    if scope == "unit":
        return f"unit:{target['unit_id']}"
    lo, hi = target["chapters"]
    return f"chapters:{target['unit_id']}:{lo}-{hi}"


def compute_preview(book: str, scope: str, target: dict[str, Any],
                    content: str, input_version: str, rebase: bool = False,
                    *, current_version: str | None = None) -> dict[str, Any]:
    """影子合并 + 规范化候选 + 影响与 preview_digest（不写盘、不调模型）。

    input_version 与当前版本不同且未显式 rebase → `PLANNING_INPUT_CHANGED`，
    保留候选交回作者决定（规格08 §3.1）。
    """
    current = current_version or version_etag(book)
    if input_version != current and not rebase:
        raise PlanningInvalid(
            "输入版本已变化（正式档案被修改过）。候选保留；如需按当前版本重建差异，"
            "请显式以 rebase=true 重新预览", code="PLANNING_INPUT_CHANGED")
    candidate = parse_candidate(content, scope)
    # 包络 target 与请求 target 必须一致（不接受跨书/错层目标）
    if candidate["target"] != target:
        raise PlanningInvalid("包络 target 与请求 target 不一致",
                              code="PLANNING_RANGE_INVALID")
    merged = merge_candidate(book, scope, target, candidate["data"])
    content_digest, parent_digest = _layer_projection(scope, merged, target)

    # 规范化候选：含程序分配的新卷号/单元ID/章号（规格08 §3.1）
    normalized = {"scope": scope, "target": target, "data": _normalized_data(
        scope, target, merged)}
    old_projection = _old_projection(book, scope, target)
    impact = merged["impact"]
    preview_digest = digest({
        "scope": scope, "target": target,
        "candidate": normalized["data"],
        "input_version": input_version, "preview_version": current,
        "rebase": bool(rebase), "impact": impact,
    })
    return {
        "scope": scope, "target": target,
        "candidate": normalized,
        "old_projection": old_projection,
        "new_projection": _new_projection(scope, merged, target),
        "impact": impact,
        "content_digest": content_digest,
        "parent_digest": parent_digest,
        "input_version": input_version,
        "preview_version": current,
        "rebase": bool(rebase),
        "baseline_changed": input_version != current,
        "preview_digest": preview_digest,
        "warnings": ([] if input_version == current
                     else ["基线已变：预览按当前版本重建，原候选 input_version 保留"]),
    }


def _normalized_data(scope: str, target: dict[str, Any],
                     merged: dict[str, Any]) -> dict[str, Any]:
    """把程序分配的编号/范围放进规范化候选 data。"""
    planning = merged["new_planning"]
    fm = merged["new_fm"]
    if scope == "global":
        volumes = [
            {"no": v.get("no"), "name": v.get("name", ""),
             "direction": v.get("direction", ""),
             "estimated_chapters": v.get("estimated_chapters"),
             "chapters": v.get("chapters") or [],
             "detail_level": v.get("detail_level", "rough")}
            for v in fm.get("volumes") or []]
        return {"logline": fm.get("logline", ""),
                "ending_direction": fm.get("ending_direction", ""),
                "global": planning.get("global") or {}, "rough_volumes": volumes}
    if scope == "volume":
        no = str(target["volume_no"])
        vol = next(v for v in fm.get("volumes") or [] if str(v.get("no")) == no)
        units = [{"id": u["id"], "name": u.get("name", ""), "chapters": u.get("chapters") or []}
                 for u in planning.get("units") or [] if str(u.get("volume_no")) == no]
        return {"chapter_count": vol["chapters"][1] - vol["chapters"][0] + 1,
                "volume_detail": {k: vol.get(k, "") for k in VOLUME_DETAIL_FIELDS},
                "volume_range": vol["chapters"], "unit_skeletons": units}
    if scope == "unit":
        uid = target["unit_id"]
        unit = next(u for u in planning.get("units") or [] if str(u.get("id")) == uid)
        return {"unit_detail": {k: unit.get(k) for k in
                                ("goal", "obstacle", "choices", "outcome",
                                 "can_reveal", "must_hold", "story_lines")}}
    lo, hi = target["chapters"]
    rows = [{c: str(r.get(c, "")) for c in outline.CHAPTER_COLS}
            for r in merged["rows"]
            if (m := re.search(r"\d+", str(r.get("章", "")))) and lo <= int(m.group()) <= hi]
    return {"chapter_rows": rows, "rhythm_note": rhythm_batch(
        merged["rhythm"], target["unit_id"], lo, hi)}


def _old_projection(book: str, scope: str, target: dict[str, Any]) -> dict[str, Any]:
    try:
        info = read_planning(book)
        fm = read_outline_full(book)
        planning = info["planning"]
    except PlanningInvalid:
        return {}
    if scope == "global":
        return project_global(fm, planning)
    if scope == "volume":
        try:
            return project_volume(fm, planning, str(target["volume_no"]))
        except PlanningInvalid:
            return {}
    if scope == "unit":
        try:
            return project_unit(planning, str(target["unit_id"]))
        except PlanningInvalid:
            return {}
    lo, hi = target["chapters"]
    return _project_batch(fm, planning, str(target["unit_id"]), lo, hi)


def _new_projection(scope: str, merged: dict[str, Any],
                    target: dict[str, Any]) -> dict[str, Any]:
    fm = dict(merged["new_fm"])
    fm["chapter_rows"] = merged["rows"]
    fm["_rhythm"] = merged["rhythm"]
    planning = merged["new_planning"]
    if scope == "global":
        return project_global(fm, planning)
    if scope == "volume":
        return project_volume(fm, planning, str(target["volume_no"]))
    if scope == "unit":
        return project_unit(planning, str(target["unit_id"]))
    lo, hi = target["chapters"]
    return _project_batch(fm, planning, str(target["unit_id"]), lo, hi)


def adopt_preview(book: str, scope: str, target: dict[str, Any], content: str,
                  input_version: str, preview_digest: str,
                  rebase: bool = False, *, source: str = "ai") -> dict[str, Any]:
    """采纳：服务端重算同一预览并比对，写后读回核对（规格08 §3.1）。

    调用方（api 层）负责：If-Match == 当前版本（412）、互斥锁与门禁检查。
    """
    current = version_etag(book)
    preview = compute_preview(book, scope, target, content, input_version,
                              rebase, current_version=current)
    if preview["preview_digest"] != preview_digest:
        raise PlanningInvalid(
            "候选或预览已变化（可能被编辑过），旧预览不可直接采纳；请重新预览确认",
            code="PLANNING_PREVIEW_CHANGED")
    gate = generation_gate(book, scope, target)
    if not gate["allowed"]:
        raise PlanningInvalid(gate["message"] or "规划门禁未通过", code=gate["code"])

    merged = merge_candidate(book, scope, target, parse_candidate(content, scope)["data"])
    content_digest, parent_digest = _layer_projection(scope, merged, target)
    planning = merged["new_planning"]
    adoptions = planning.setdefault("adoptions", {})
    key = _scope_key(scope, target)
    # 章纲批次记录不重叠：子范围修改更新既有记录（规格08 §4.6）
    if scope == "chapters":
        lo, hi = target["chapters"]
        uid = str(target["unit_id"])
        for old_key in list(adoptions):
            m = re.fullmatch(rf"chapters:{re.escape(uid)}:([0-9]+)-([0-9]+)", str(old_key))
            if m and not (int(m.group(2)) < lo or int(m.group(1)) > hi):
                del adoptions[old_key]
    adoptions[key] = {
        "content_digest": content_digest,
        "parent_digest": parent_digest,
        "accepted_at": _now_iso(),
        "source": source,
    }
    outline.write_outline(book, merged["new_fm"], rows=merged["rows"],
                          rhythm=merged["rhythm"])
    # 写后读回核对：读回投影必须与采纳时一致，否则不返回成功（规格08 §5）
    verify = _new_projection(scope, {
        "new_fm": {k: v for k, v in read_outline_full(book).items()
                   if not k.startswith("_")},
        "rows": read_outline_full(book)["chapter_rows"],
        "rhythm": str(read_outline_full(book).get("_rhythm") or ""),
        "new_planning": read_planning(book)["planning"],
    }, target)
    if digest(verify) != content_digest:
        raise PlanningInvalid(
            "OUTLINE.md 写入后读回核对失败（内容与采纳预览不一致），请检查文件后重试",
            code="PLANNING_INVALID")
    impact = merged["impact"]
    return {
        "updated_scope": scope, "target": target,
        "added_numbers": impact.get("added_numbers") or [],
        "changed_numbers": impact.get("changed_numbers") or [],
        "volumes_added": impact.get("volumes_added") or [],
        "units_added": impact.get("units_added") or [],
        "readiness": readiness(book),
        "next_actions": next_actions(book),
        "etag": version_etag(book),
    }


# ---------------------------------------------------------------------------
# 生成（POST /planning/generate：走既有 G5 调用链，generation_log 必记）
# ---------------------------------------------------------------------------

def _realm_guard(book: str) -> str:
    """G5 的境界口径硬约束（规格08 §6.4 真模型对比暴露：规划层会写出越级境界）。

    境界清单取自 STORY_BIBLE 力量体系表；当前境界取自 STATE.md 的最近快照。
    取值失败就只给清单，**不猜**当前境界（宁缺勿假）。
    """
    try:
        from . import bible as _bible

        rows = _bible.power_rows(book)
    except Exception:
        return ""
    realms: list[str] = []
    for row in rows or []:
        big = str(row.get("大境界") or "").replace("**", "").strip()
        sub = str(row.get("子境界") or "").replace("**", "").strip()
        if big:
            realms.append(f"{big}（{sub}）" if sub else big)
    if not realms:
        return ""
    text = "力量体系口径（硬约束）：境界名只能取 " + "、".join(realms) + " 之一"
    try:
        from . import state as _state

        latest = _state.latest_map(book)
        bits = [f"{name}＝{str((snap or {}).get('power_level') or '').strip()}"
                for name, snap in list(latest.items())[:4]
                if str((snap or {}).get("power_level") or "").strip()]
        if bits:
            text += "；已生效快照境界：" + "、".join(bits)
    except Exception:
        pass
    return text + "。禁止越级使用能力、禁止叙述尚未发生的突破；境界不明时按快照写。"


def generate_variables(book: str, scope: str, target: dict[str, Any],
                       requirements: str = "", *, projection: dict[str, Any] | None = None) -> dict[str, Any]:
    """按作用域装配 G5 变量（含 legacy 兼容默认值）。

    生成固定原书、作用域、参数和 input_version（规格08 §3.1）。
    """
    from . import bible, characters

    if projection is not None:
        od = projection["outline"]
        cfg = books.load_config(book)
        wr = cfg["chapter_word_range"]
        from .mdtable import parse_table

        _, power = parse_table(projection["bible"].get("力量体系", ""), "大境界")
        scoped_outline = {"logline": od.get("logline"), "ending_direction": od.get("ending_direction"),
                          "global": od.get("planning", {}).get("global") or {},
                          "volume": next((v for v in od.get("volumes") or []
                                          if str(v.get("no")) == str(target.get("volume_no"))), {})}
        if scope in {"unit", "chapters"}:
            scoped_outline["unit"] = next((u for u in od.get("planning", {}).get("units") or []
                                           if u.get("id") == target.get("unit_id")), {})
        if scope == "chapters":
            lo, hi = target["chapters"]
            scoped_outline["chapter_rows"] = [r for r in od.get("chapter_rows") or []
                                               if lo <= (outline._row_no(r.get("章")) or 0) <= hi]
        return {
            "规划作用域": scope,
            "规划上下文": "卷联合候选内存投影，未人工采纳；计划不是已写事实。\n"
                + "target 回显：" + json.dumps(target, ensure_ascii=False)
                + "\n当前候选规划：" + json.dumps(scoped_outline, ensure_ascii=False)
                + "\n力量体系口径（硬约束）：" + json.dumps(power, ensure_ascii=False)
                + "\n作者要求：" + requirements,
            "世界观摘要": json.dumps(projection["bible"], ensure_ascii=False),
            "角色卡摘要": json.dumps({name: {key: card.get(key) for key in ("role", "status")}
                                      for name, card in projection["characters"].items()}, ensure_ascii=False),
            "logline": od.get("logline", ""), "目标总章数": str(max(
                [outline._row_no(r.get("章")) or 0 for r in od.get("chapter_rows") or []]
                + [int(v.get("chapters", [0, 0])[-1]) for v in od.get("volumes") or [] if v.get("chapters")]
                + [0])),
            "单章字数区间": f"{wr[0]}-{wr[1]}",
        }

    meta = books.read_meta(book)
    cfg = books.load_config(book)
    wr = cfg.get("chapter_word_range") or meta.get("chapter_word_range") or [2200, 2800]
    est = estimates(book)
    try:
        bible_summary = "\n".join(
            f"【{k}】{str(v)[:600]}" for k, v in bible.read(book).items()
            if "自检" not in k) or "（世界观圣经暂为空）"
    except Exception:
        bible_summary = "（世界观圣经暂为空）"
    try:
        chars_summary = "\n".join(
            f"{n}（{characters.read(book, n).get('role', '')}）"
            for n in characters.list_characters(book)) or "（暂无角色档案）"
    except Exception:
        chars_summary = "（暂无角色档案）"

    scope_desc = {
        "global": "global：全书粗纲（logline/终点/读者承诺/卖点/主线/阶段边界/各卷粗方向与估算章数）",
        "volume": f"volume：卷 {target.get('volume_no')} 的卷纲（目标/阻力/转折/高潮/钩子/预期末态 + 单元骨架）",
        "unit": f"unit：单元 {target.get('unit_id')} 的详情（目标/阻力/选择/结果/信息边界）",
        "chapters": (f"chapters：单元 {target.get('unit_id')} 第 "
                     f"{(target.get('chapters') or [0, 0])[0]}-{(target.get('chapters') or [0, 0])[1]} 章"
                     "的七列章纲批次"),
    }[scope]
    # 境界口径对所有非 global 作用域都生效：下层写错境界会一路污染到正文
    realm_line = _realm_guard(book) if scope != "global" else ""

    if scope == "global":
        ctx = (f"已确认的规划状态：无（首次规划）或见下。\n"
               f"目标字数 {meta.get('target_words')}；单章字数区间 {wr[0]}-{wr[1]}；"
               f"估算总章数 {est.get('estimated_chapters') or '未知'}（仅供粗规划参考）。\n"
               f"作者补充要求：{requirements or '（无）'}")
    elif scope == "volume":
        tr = tree(book)
        vol = next((v for v in tr.get("volumes") or []
                    if str(v.get("no")) == str(target.get("volume_no"))), {})
        ctx = (f"全书粗纲（已人工采纳）：logline={tr.get('global', {}).get('logline', '')}；"
               f"主线={tr.get('global', {}).get('planning', {}).get('mainline', '')}\n"
               f"目标卷：卷{vol.get('no')}{vol.get('name', '')}，方向={vol.get('direction', '')}，"
               f"估算 {vol.get('estimated_chapters')} 章\n"
               f"已有正文/已规划最大章号：{max(tr.get('chapter_numbers') or [0]) or 0}\n"
               f"{realm_line + chr(10) if realm_line else ''}"
               f"作者补充要求：{requirements or '（无）'}")
    elif scope == "unit":
        info = read_planning(book)
        unit = next((u for u in info["planning"].get("units") or []
                     if str(u.get("id")) == str(target.get("unit_id"))), {})
        vol = next((v for v in read_outline_full(book).get("volumes") or []
                    if str(v.get("no")) == str(unit.get("volume_no"))), {})
        ctx = (f"所属卷（已采纳）：卷{vol.get('no')}《{vol.get('name', '')}》"
               f"目标={vol.get('goal', '')}｜核心冲突={vol.get('core_conflict', '')}｜"
               f"卷末高潮={vol.get('climax', '')}\n"
               f"目标单元：{unit.get('name', '')}（单元ID {unit.get('id')}，章 {unit.get('chapters')}）\n"
               f"target 回显：{{\"volume_no\": {target.get('volume_no')}, "
               f"\"unit_id\": \"{target.get('unit_id')}\", \"chapters\": null}}\n"
               f"同卷其他单元只作背景短摘要，不要重写。\n"
               f"{realm_line + chr(10) if realm_line else ''}"
               f"作者补充要求：{requirements or '（无）'}")
    else:  # chapters
        info = read_planning(book)
        unit = next((u for u in info["planning"].get("units") or []
                     if str(u.get("id")) == str(target.get("unit_id"))), {})
        lo, hi = target.get("chapters") or [0, 0]
        existing = [r for r in read_outline_full(book).get("chapter_rows") or []
                    if (m := re.search(r"\d+", str(r.get("章", "")))) and lo <= int(m.group()) <= hi]
        ctx = (f"单元（已采纳）：{unit.get('name', '')}（单元ID {unit.get('id')}），"
               f"目标={unit.get('goal', '')}｜"
               f"阻力={unit.get('obstacle', '')}｜收束={unit.get('outcome', '')}\n"
               f"可释放信息：{unit.get('can_reveal') or []}；必须保留：{unit.get('must_hold') or []}\n"
               f"本批已有行（可整批替换）：{len(existing)} 行\n"
               f"target 回显：{{\"volume_no\": {target.get('volume_no')}, "
               f"\"unit_id\": \"{target.get('unit_id')}\", \"chapters\": [{lo}, {hi}]}}\n"
               f"相邻已写章摘要：见上下文，不把未来章当事实。\n"
               f"{realm_line + chr(10) if realm_line else ''}"
               f"作者补充要求：{requirements or '（无）'}")

    return {
        "规划作用域": scope_desc,
        "规划上下文": ctx,
        "世界观摘要": bible_summary,
        "角色卡摘要": chars_summary,
        "logline": str(read_outline_full(book).get("logline") or ""),
        "目标总章数": str(est.get("estimated_chapters") or ""),
        "单章字数区间": f"{wr[0]}-{wr[1]}",
    }
