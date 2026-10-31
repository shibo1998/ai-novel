"""（规格10 §2.4）有来源与时点的批次待审事实；永不写正式记忆。"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Iterator

from . import atomic, bible, books, chapters, characters, frontmatter as FM, mechcheck, outline, state


class DraftContextError(RuntimeError):
    """临时事实损坏、过期或矛盾，必须暂停批次。"""

    code = "PRODUCTION_STALE"


_SCOPE: ContextVar[dict[str, Any] | None] = ContextVar("production_draft_context", default=None)


def outline_fingerprint(book: str, no: int) -> str:
    """（规格10 §2.4）完整细纲文件的 SHA-256，包含人工确认字段。"""
    return hashlib.sha256(chapters.outline_path(book, no).read_bytes()).hexdigest()


def _path(book: str, job_dir: str | Path) -> Path:
    root = (books.book_dir(book) / "production").resolve()
    directory = Path(job_dir).resolve()
    if directory == root or not directory.is_relative_to(root):
        raise DraftContextError("临时上下文目录不在本书受控 production 子目录")
    path = directory / "draft_context.json"
    if not path.resolve().is_relative_to(directory):
        raise DraftContextError("临时上下文文件越出任务目录")
    return path


def _dependencies(book: str) -> str:
    root = books.book_dir(book)
    names = ["meta.md", "config.json", "STORY_BIBLE.md", "OUTLINE.md", "PLOT_POINTS.md", "LOCATIONS.md",
             "STATE.md", "STATE.accepted.json"]
    paths = [root / name for name in names] + sorted((root / "characters").glob("char-*.md"))
    values = [(p.relative_to(root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest()
               if p.exists() else None) for p in paths]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode("utf-8")).hexdigest()


def _read(book: str, job_dir: str | Path) -> dict[str, Any]:
    path = _path(book, job_dir)
    if not path.exists():
        return {"schema_version": 1, "book": book, "items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DraftContextError("临时上下文无法读取") from error
    if (not isinstance(data, dict) or type(data.get("schema_version")) is not int
            or data["schema_version"] != 1 or data.get("book") != book
            or not isinstance(data.get("items"), list)):
        raise DraftContextError("临时上下文 schema/book 非法")
    seen: set[int] = set()
    for item in data["items"]:
        if (not isinstance(item, dict) or type(item.get("no")) is not int or item["no"] <= 0
                or item["no"] in seen):
            raise DraftContextError("临时上下文章号非法或重复")
        seen.add(item["no"])
    return data


def _status_map(book: str, no: int) -> dict[str, dict[str, Any]]:
    result = {name: dict(card.get("status") or {}) for name, card in characters.read_all(book).items()}
    for name, row in state.confirmed_map(book, no - 1).items():
        if name in result:
            result[name].update({"realm": row.get("power_level", ""), "location": row.get("location", ""),
                                 "emotion": row.get("mental", "")})
    return result


def apply_fo_ops(rows: list[dict[str, Any]], ops: list[dict[str, Any]], no: int,
                 *, allow_plant: bool = True) -> list[dict[str, Any]]:
    """（规格10 §2.3/§2.4）纯内存 FO 顺序验证；paid 不回退、不消费未来编号。"""
    result = [dict(row) for row in rows]
    by_id = {row.get("编号"): row for row in result}
    if not isinstance(ops, list):
        raise DraftContextError("FO 操作必须是列表")
    for op in ops:
        if not isinstance(op, dict):
            raise DraftContextError("FO 操作必须是对象")
        fid, kind = op.get("fo_id"), op.get("op")
        if not re.fullmatch(r"FO-\d{3}", str(fid or "")) or kind not in {"plant", "advance", "pay"}:
            raise DraftContextError("FO 编号或操作非法")
        row = by_id.get(fid)
        if kind == "plant":
            if not allow_plant or row is not None or not isinstance(op.get("note"), str) or not op["note"].strip():
                raise DraftContextError(f"{fid} 不允许新建/重复埋设或缺少内容")
            row = {"编号": fid, "内容": op["note"], "埋设章": str(no), "推进章": "",
                   "预计回收章": str(op.get("expected_pay") or ""), "状态": "planted", "备注": ""}
            result.append(row)
            by_id[fid] = row
            continue
        if row is None or (str(row.get("埋设章") or "").isdigit() and int(row["埋设章"]) > no):
            raise DraftContextError(f"{fid} 不存在或在未来才埋设")
        if kind == "advance":
            if row.get("状态") not in {"planted", "building"}:
                raise DraftContextError(f"{fid} 已回收，不能推进")
            row["状态"] = "building"
            row["推进章"] = ",".join(filter(None, [str(row.get("推进章") or ""), str(no)]))
        else:
            if row.get("状态") != "building":
                raise DraftContextError(f"{fid} 回收要求 building")
            row["状态"] = "paid"
    return result


def _changes(book: str, memory: dict[str, Any], no: int, statuses: dict[str, dict[str, Any]],
             state_map: dict[str, dict[str, Any]], plots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from . import pipeline

    chapters.validate_memory(memory)
    pipeline._validate_memory_items(memory)
    if memory.get("chapter_no") != no:
        raise DraftContextError("临时记忆 chapter_no 与来源章不一致")
    if any(memory.get(key) for key in ("new_chars", "new_locations", "new_settings_pending", "timeline_candidates")):
        raise DraftContextError("临时记忆含新实体/设定或时间线候选，需人工处理")
    locations = {row.get("名称") for row in outline.read_locations(book)}
    ladder = bible.realm_order(book)
    declared = {(op.get("fo_id"), op.get("op")) for op in
                chapters.read_outline_contract(book, no).get("foreshadow_ops") or []}
    for op in memory.get("foreshadow_ops") or []:
        if op.get("op") == "plant" and (op.get("fo_id"), "plant") not in declared:
            raise DraftContextError("V4 新建了未在已确认合同声明的 FO 实体")
    inventory_terms = set(bible.terms(book)) | {item for status in statuses.values()
                                              for item in status.get("inventory") or []}
    updates: dict[str, dict[str, Any]] = {}
    for item in memory.get("char_updates") or []:
        name, changes = item.get("name"), item.get("changes")
        if name not in statuses or not isinstance(changes, dict):
            raise DraftContextError("临时人物更新引用未知角色或非法 changes")
        if set(changes) - set(characters.STATUS_FIELDS) or "updated_chapter" in changes:
            raise DraftContextError("临时人物更新包含非法状态字段")
        target = updates.setdefault(name, {})
        for key, value in changes.items():
            if key in target and target[key] != value:
                raise DraftContextError(f"{name} 同章状态更新自相矛盾")
            target[key] = value
    state_entries: dict[str, dict[str, Any]] = {}
    for entry in memory.get("state_delta") or []:
        name = entry.get("character") or entry.get("name")
        if name not in statuses:
            raise DraftContextError(f"临时快照引用未知角色 {name}")
        allowed = {"character", "name", "location", "physical", "mental", "power_level", "arc_stage",
                   "knowledge", "goals", "inventory", "alive", "injuries"}
        if set(entry) - allowed:
            raise DraftContextError("临时快照包含未知字段")
        seen = state_entries.setdefault(name, {})
        for key, value in entry.items():
            if key in seen and seen[key] != value:
                raise DraftContextError(f"{name} 同章临时快照自相矛盾")
            seen[key] = value
        row = dict(state_map.get(name) or {})
        row.update(entry, character=name, chapter_no=no)
        state_map[name] = row
        changes = updates.setdefault(name, {})
        for key, field in (("location", "location"), ("power_level", "realm"), ("mental", "emotion"),
                           ("knowledge", "known_info"), ("inventory", "inventory"), ("alive", "alive"),
                           ("injuries", "injuries")):
            if key in entry:
                if field in changes and changes[field] != entry[key]:
                    raise DraftContextError(f"{name} char_updates/state_delta 的 {field} 矛盾")
                changes[field] = entry[key]
        if "arc_stage" in entry and (type(entry["arc_stage"]) is not int or entry["arc_stage"] < 0):
            raise DraftContextError("临时弧光阶段非法")
        for field in ("knowledge", "goals", "inventory", "injuries"):
            if field in entry and (not isinstance(entry[field], list) or any(not isinstance(v, str) for v in entry[field])):
                raise DraftContextError(f"临时快照 {field} 必须为文本列表")
    for name, changes in updates.items():
        old = statuses[name]
        for field, value in changes.items():
            if field in {"injuries", "inventory", "known_info"}:
                if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                    raise DraftContextError(f"{name}.{field} 必须为文本列表")
            elif field == "alive":
                if type(value) is not bool or (old.get("alive") is False and value is True):
                    raise DraftContextError(f"{name} 生死状态非法/复活")
            elif not isinstance(value, str):
                raise DraftContextError(f"{name}.{field} 必须为文本")
        place = changes.get("location")
        if place and place not in locations:
            raise DraftContextError(f"未知地点 {place}，不可注册为临时事实")
        if any(item not in inventory_terms for item in changes.get("inventory") or []):
            raise DraftContextError(f"{name} 持有物包含未建档的新实体")
        realm = changes.get("realm")
        if realm and realm not in pipeline._NON_REALM_LABELS:
            ni = mechcheck.realm_index(realm, ladder)
            oi = mechcheck.realm_index(str(old.get("realm") or ""), ladder)
            if ni is None or (oi is not None and ni < oi):
                raise DraftContextError(f"{name} 境界未知或倒退：{realm}")
        if realm in pipeline._NON_REALM_LABELS and mechcheck.realm_index(str(old.get("realm") or ""), ladder) is not None:
            raise DraftContextError(f"{name} 境界退回普通人状态")
        old.update(changes, updated_chapter=no)
        row = state_map.setdefault(name, {"character": name})
        row.update(chapter_no=no, location=old.get("location", ""), power_level=old.get("realm", ""),
                   mental=old.get("emotion", ""), knowledge=old.get("known_info", []),
                   inventory=old.get("inventory", []), alive=old.get("alive", True))
    return apply_fo_ops(plots, memory.get("foreshadow_ops") or [], no)


def load_for_chapter(book: str, job_dir: str | Path, no: int) -> dict[str, Any]:
    """（规格10 §2.4）只消费严格早于本章、正文/完整合同及档案指纹匹配的记忆。"""
    if type(no) is not int or no <= 0:
        raise DraftContextError("当前章号非法")
    data = _read(book, job_dir)
    statuses = _status_map(book, no)
    snapshots = state.confirmed_map(book, no - 1)
    plots = outline.read_plot_points(book)
    items: list[dict[str, Any]] = []
    for item in sorted(data["items"], key=lambda value: value["no"]):
        if item["no"] >= no:
            continue
        source_no = item["no"]
        try:
            prose = chapters.read_prose(book, source_no)
            valid = (item.get("body_fingerprint") == mechcheck.fingerprint(prose["_body"])
                     and item.get("outline_fingerprint") == outline_fingerprint(book, source_no)
                     and item.get("dependencies") == _dependencies(book)
                     and isinstance(item.get("memory"), dict))
        except (OSError, FM.FrontmatterError) as error:
            raise DraftContextError(f"第{source_no}章上下文来源缺失") from error
        if not valid:
            raise DraftContextError(f"第{source_no}章正文/合同/依赖已变化，后续待复核")
        from . import pipeline, statemachine, volumeproduction

        if statemachine.current_status(book, source_no) not in {"pending_finalize", "finalized"}:
            raise DraftContextError(f"第{source_no}章已不是已检查待审稿")
        pipeline._assert_current_check(book, source_no, prose["_body"])
        volumeproduction._review(book, source_no, item["body_fingerprint"])
        plots = _changes(book, item["memory"], source_no, statuses, snapshots, plots)
        items.append(dict(item, body=prose["_body"]))
    sources = [{key: item[key] for key in ("no", "body_fingerprint", "outline_fingerprint")} for item in items]
    previous = next((item["body"] for item in items if item["no"] == no - 1), "")
    if no > 1 and not previous and chapters.prose_path(book, no - 1).exists():
        prose = chapters.read_prose(book, no - 1)
        if prose.get("status") != "final":
            raise DraftContextError("前章待审稿未在本批临时上下文中验证，禁止作为已写事实继续")
        previous = prose["_body"]
    return {"book": book, "no": no, "items": items, "sources": sources, "statuses": statuses,
            "state_map": snapshots, "plot_rows": plots, "previous_body": previous}


def current(book: str, no: int | None = None) -> dict[str, Any] | None:
    scope = _SCOPE.get()
    if scope and scope["book"] == book and (no is None or scope.get("no") == no):
        return scope.get("context")
    return None


def before_call(book: str, no: int | None, stage: str) -> None:
    """（规格10 §1）每次真实调用之前检查暂停；不吞父级暂停异常。"""
    scope = _SCOPE.get()
    if scope and scope["book"] == book:
        if scope.get("check_stop"):
            scope["check_stop"](stage, no)
        if scope.get("job_dir") is not None and scope.get("context") is not None:
            expected = scope["context"].get("_current_outline_fingerprint")
            if expected and expected != outline_fingerprint(book, scope["no"]):
                raise DraftContextError("本章已确认合同在请求间发生变化")
            load_for_chapter(book, scope["job_dir"], scope["no"])


@contextmanager
def scoped(book: str, job_dir: str | Path | None = None, no: int | None = None, *,
           context: dict[str, Any] | None = None,
           check_stop: Callable[[str, int | None], None] | None = None) -> Iterator[dict[str, Any] | None]:
    """（规格10 §4）可嵌套、线程隔离；退出后单章调用不残留批次标记。"""
    previous = _SCOPE.get()
    if context is None and job_dir is not None and no is not None:
        context = load_for_chapter(book, job_dir, no)
    if context is not None and no is not None and chapters.outline_path(book, no).exists():
        context = dict(context, _current_outline_fingerprint=outline_fingerprint(book, no))
    if check_stop is None and previous and previous["book"] == book:
        check_stop = previous.get("check_stop")
    token = _SCOPE.set({"book": book, "job_dir": job_dir, "no": no,
                        "context": context, "check_stop": check_stop})
    try:
        yield context
    finally:
        _SCOPE.reset(token)


scope = scoped


def evidence(context: dict[str, Any] | None) -> str:
    if not context or not context["items"]:
        return ""
    return "【批次待审事实：仅此前真实已检查稿，非正式正史】\n" + json.dumps(
        [{"source": source, "memory": item["memory"]} for source, item in zip(context["sources"], context["items"])],
        ensure_ascii=False)


def mech_context(book: str, no: int, canonical: dict[str, Any]) -> dict[str, Any]:
    """（规格10 §2.4）用来源明确的前章临时状态替代旧快照，不伪造本章抽取结果。"""
    context = current(book, no)
    if context is None:
        return canonical
    result = dict(canonical)
    previous = dict(context["state_map"])
    # 本章尚未抽取：仅移入有真实前章 V4 来源的状态，不能硬造测试快照。
    result["rows"] = [dict(row) for row in previous.values()]
    result["prev"] = previous
    result["has_state"] = bool(previous)
    result["char_status"] = {name: "死亡" if status.get("alive") is False else "存活"
                             for name, status in context["statuses"].items()}
    result["char_death"] = {name: status.get("updated_chapter", 0) for name, status in context["statuses"].items()
                            if status.get("alive") is False}
    if context["previous_body"]:
        result["prev_ending"] = context["previous_body"].strip()[-400:]
    return result


def collect(book: str, job_dir: str | Path, no: int, client: Any = None) -> dict[str, Any]:
    """（规格10 §2.4）复用 V4 抽取并验证，只原子保存本批 JSON，不调用 _apply_memory。"""
    from . import pipeline, statemachine

    if statemachine.current_status(book, no) != "pending_finalize":
        raise DraftContextError("仅 pending_finalize 已检查稿可抽取临时记忆")
    prose = chapters.read_prose(book, no)
    pipeline._assert_current_check(book, no, prose["_body"])
    from . import volumeproduction

    volumeproduction._review(book, no, mechcheck.fingerprint(prose["_body"]))
    before = load_for_chapter(book, job_dir, no)
    data = _read(book, job_dir)
    body_fp, contract_fp = mechcheck.fingerprint(prose["_body"]), outline_fingerprint(book, no)
    existing = next((item for item in data["items"] if item["no"] == no), None)
    if existing:
        if (existing.get("body_fingerprint") != body_fp or existing.get("outline_fingerprint") != contract_fp
                or existing.get("dependencies") != _dependencies(book)):
            raise DraftContextError("本章旧临时记忆已过期，不静默替换后续来源")
        _changes(book, existing["memory"], no, before["statuses"], before["state_map"], before["plot_rows"])
        return existing
    with scoped(book, job_dir, no, context=before):
        memory = pipeline._extract_memory(book, no, prose, client=client)
    _changes(book, memory, no, before["statuses"], before["state_map"], before["plot_rows"])
    if body_fp != mechcheck.fingerprint(chapters.read_prose(book, no)["_body"]) or contract_fp != outline_fingerprint(book, no):
        raise DraftContextError("抽取期间正文或合同变化")
    item = {"no": no, "body_fingerprint": body_fp, "outline_fingerprint": contract_fp,
            "dependencies": _dependencies(book), "memory": memory}
    data["items"].append(item)
    data["items"].sort(key=lambda value: value["no"])
    atomic.write_text(_path(book, job_dir), json.dumps(data, ensure_ascii=False, indent=2))
    return item
