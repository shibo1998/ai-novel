"""（规格10 §2/3）文件为真相的卷生产任务；人工确认、检查点与可恢复提交。"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import threading
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from . import atomic, books, chapters, conflicts, db, frontmatter as FM, operations, outline, paths, planning, statemachine

STATES = {"preparing", "awaiting_outline_confirmation", "generating", "awaiting_final_review", "applied", "partially_applied",
          "paused", "error", "interrupted", "cancelled", "stale", "recovery_required"}
TERMINAL = {"applied", "cancelled", "stale"}
_GUARD = threading.RLock()
_ACTIVE: set[str] = set()
_CONTROLS: dict[str, str] = {}


class ProductionError(RuntimeError):
    """（规格10 §3）可公开的稳定错误，不回显模型连接/鉴权信息。"""
    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code, self.status = code, status


class Paused(RuntimeError):
    """（规格10 §2.2）安全检查点暂停，不是模型调用失败。"""


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()


def _uuid(value: str) -> str:
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ProductionError("PRODUCTION_ID_INVALID", "任务/请求标识必须是标准小写UUID", 422) from None
    return value


def _root(book: str) -> Path:
    root = books.book_dir(book).resolve()
    if not (root / "meta.md").is_file():
        raise ProductionError("BOOK_NOT_FOUND", "书籍不存在", 404)
    return root


def directory(book: str, job_id: str) -> Path:
    root = _root(book)
    result = root / "production" / _uuid(job_id)
    if not result.resolve().is_relative_to(root):
        raise ProductionError("PRODUCTION_PATH_UNSAFE", "任务路径越界", 422)
    return result


def _key(book: str, job_id: str) -> str:
    return str(directory(book, job_id).resolve()).casefold()


def _read(path: Path) -> str | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8", newline="") as stream:
        return stream.read()


def dependencies(book: str) -> dict[str, str | None]:
    """（规格10 §2.4）固定档案/模板/词表指纹；不包含凭据和本任务输出。"""
    root = _root(book)
    files = [root / name for name in ("meta.md", "config.json", "STORY_BIBLE.md", "OUTLINE.md", "PLOT_POINTS.md", "LOCATIONS.md", "STATE.md", "TIMELINE.md")]
    for folder in ("characters", "style", "prompts"):
        files.extend(p for p in (root / folder).rglob("*") if p.is_file())
    entries = {"book/" + str(p.relative_to(root)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None for p in files}
    for folder in ("prompts", "lint"):
        for p in (paths.project_root() / folder).rglob("*"):
            if p.is_file():
                entries[folder + "/" + str(p.relative_to(paths.project_root() / folder)).replace("\\", "/")] = hashlib.sha256(p.read_bytes()).hexdigest()
    return entries


def book_etag(book: str) -> str:
    return f'"{digest(dependencies(book))}"'


def _etag(job: dict[str, Any]) -> str:
    return f'"{digest(job)}"'


def check_version(expected: str | None, actual: str) -> None:
    if expected is None:
        raise ProductionError("PRODUCTION_PRECONDITION_REQUIRED", "请先读取当前版本再操作", 428)
    if expected.strip() != actual:
        raise ProductionError("PRODUCTION_CHANGED", "输入或任务已变化，未覆盖，请保留编辑并刷新核对", 412)


def _load(book: str, job_id: str) -> dict[str, Any]:
    path = directory(book, job_id) / "manifest.json"
    try:
        text = _read(path)
        if text is None:
            raise ProductionError("PRODUCTION_NOT_FOUND", "任务不存在", 404)
        job = json.loads(text)
        if (not isinstance(job, dict) or type(job.get("schema_version")) is not int or job["schema_version"] != 1 or job.get("id") != job_id
                or job.get("book") != book or not isinstance(job.get("status"), str) or job["status"] not in STATES):
            raise ValueError
        for field in ("request", "dependencies", "checkpoints", "action_requests", "recovery"):
            if not isinstance(job.get(field), dict):
                raise ValueError
        for field in ("numbers", "completed", "applied"):
            if not isinstance(job.get(field), list) or any(type(no) is not int or no < 1 for no in job[field]):
                raise ValueError
        numbers = job["numbers"]
        if not numbers or len(numbers) > 50 or numbers != list(range(numbers[0], numbers[-1] + 1)):
            raise ValueError
        if not set(job["completed"] + job["applied"]).issubset(numbers):
            raise ValueError
        for field in ("phase", "reason", "created_at", "updated_at", "input_digest", "preview_digest"):
            if not isinstance(job.get(field), str):
                raise ValueError
        if job.get("current_chapter") is not None and job["current_chapter"] not in numbers:
            raise ValueError
        if any(key not in {str(no) for no in numbers} or not isinstance(value, dict) or not isinstance(value.get("fingerprint"), str)
               or not isinstance(value.get("outline_fingerprint"), str) for key, value in job["checkpoints"].items()):
            raise ValueError
        for field in ("confirmed_outlines", "boundary_sources"):
            values = job.get(field, {})
            if not isinstance(values, dict) or any(not isinstance(key, str) or (value is not None and not isinstance(value, str)) for key, value in values.items()):
                raise ValueError
        if not isinstance(job["request"].get("client_request_id"), str) or job["request"].get("mode") not in {"prepare", "append", "new_volume"}:
            raise ValueError
        return job
    except (OSError, ValueError, UnicodeError, TypeError, KeyError):
        raise ProductionError("PRODUCTION_INVALID", "任务档案损坏或版本不支持，不能继续", 503) from None


def _save(book: str, job: dict[str, Any]) -> None:
    job["updated_at"] = db.now()
    atomic.write_text(directory(book, job["id"]) / "manifest.json", json.dumps(job, ensure_ascii=False, indent=2, allow_nan=False))


def remove(book: str, job_id: str) -> dict[str, Any]:
    """删除一个已结束的任务（规格10 追加：清理半途任务）。

    只删 `books/<书>/production/<id>/` 这一个任务目录（候选、检查点、准备缓存），
    **不动 `chapters/` 下任何稿件**，也不动运行历史（generation_log 保留）。
    运行中的任务拒绝删除：先停止后续生产，等它到达安全检查点。
    """
    with _GUARD:
        job = _load(book, job_id)
        key = _key(book, job_id)
        if job["status"] in {"preparing", "generating"} or key in _ACTIVE:
            raise ProductionError(
                "PRODUCTION_RUNNING",
                "任务仍在运行；请先「停止后续生产」，等它到达安全检查点后再删除", 409)
        path = directory(book, job_id)
        _CONTROLS.pop(key, None)
        _ACTIVE.discard(key)
        shutil.rmtree(path)
        return {"ok": True, "id": job["id"], "removed": path.name}


def _public(job: dict[str, Any]) -> dict[str, Any]:
    fields = ("id", "book", "status", "phase", "numbers", "current_chapter", "completed", "applied", "reason", "request", "created_at", "updated_at", "preview_digest", "pending_control")
    return {key: copy.deepcopy(job[key]) for key in fields if key in job} | {"etag": _etag(job)}


def _source_stale(book: str, job: dict[str, Any]) -> bool:
    if job["dependencies"] != dependencies(book):
        return True
    for name, fingerprint in job.get("boundary_sources", {}).items():
        path = _root(book) / name
        if not path.resolve().is_relative_to(_root(book)):
            return True
        live = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if live != fingerprint:
            return True
    for no, fingerprint in job.get("confirmed_outlines", {}).items():
        try:
            if hashlib.sha256(chapters.outline_path(book, int(no)).read_bytes()).hexdigest() != fingerprint:
                return True
        except OSError:
            return True
    for no, checkpoint in job["checkpoints"].items():
        try:
            body = chapters.read_prose(book, int(no))["_body"]
            if hashlib.sha256(body.encode("utf-8")).hexdigest() != checkpoint["fingerprint"]:
                return True
            if hashlib.sha256(chapters.outline_path(book, int(no)).read_bytes()).hexdigest() != checkpoint["outline_fingerprint"]:
                return True
        except (OSError, ValueError, FM.FrontmatterError):
            return True
    return False


def get(book: str, job_id: str) -> dict[str, Any]:
    """（规格10 §3）刷新从文件恢复任务，不自动重发中断的付费请求。"""
    with _GUARD:
        job = _load(book, job_id)
        if _key(book, job_id) not in _ACTIVE and job["status"] in {"preparing", "generating"}:
            job["status"], job["reason"] = "interrupted", "进程已中断；已发请求结果可能不确定，请显式恢复"
            _save(book, job)
        if _key(book, job_id) not in _ACTIVE and job["status"] not in {"preparing", "generating", "cancelled", "applied", "recovery_required", "stale"} and _source_stale(book, job):
            job["status"], job["reason"] = "stale", "源档案或已出稿章节已改变，后续待复核；原稿保留"
            _save(book, job)
        if _key(book, job_id) not in _ACTIVE and any(isinstance(value, dict) and value.get("state") == "running"
                for key, value in job["action_requests"].items() if key.startswith("finalize:")):
            job.update(status="recovery_required", reason="定稿回执中断，请显式恢复核对；不会自动重调模型")
            _save(book, job)
        return _public(job)


def _jobs(book: str) -> tuple[list[dict[str, Any]], list[str]]:
    jobs, warnings = [], []
    root = _root(book) / "production"
    if root.exists():
        for folder in sorted(root.iterdir()):
            if not folder.is_dir():
                continue
            try:
                jobs.append(get(book, folder.name))
            except ProductionError:
                warnings.append(f"任务 {folder.name[:36]} 无法读取，请修复档案；未视为无任务")
    return sorted(jobs, key=lambda job: (job["created_at"], job["id"]), reverse=True), warnings


def volumes(book: str) -> dict[str, Any]:
    """（规格10 §3）卷/章节进度只读投影，不创建或采纳任务。"""
    od = outline.read_outline(book)
    rows = od["chapter_rows"]
    result = []
    for vol in od.get("volumes") or []:
        no = int(vol["no"])
        items = []
        for row in rows:
            if outline._row_no(row.get("卷")) != no:
                continue
            chapter_no = outline._row_no(row.get("章"))
            if chapter_no is None:
                continue
            body = chapters.read_prose(book, chapter_no) if chapters.prose_path(book, chapter_no).is_file() else None
            items.append({"no": chapter_no, "title": row.get("本章目标", ""), "status": statemachine.current_status(book, chapter_no),
                          "word_count": body.get("word_count", 0) if body else 0})
        result.append({"no": no, "name": str(vol.get("name") or f"第{no}卷"), "goal": str(vol.get("goal") or vol.get("direction") or ""),
                       "chapter_range": vol.get("chapters") or [], "planned_count": len(items),
                       "draft_count": sum(item["status"] != "finalized" and chapters.prose_path(book, item["no"]).exists() for item in items),
                       "finalized_count": sum(item["status"] == "finalized" for item in items), "chapters": items})
    jobs, warnings = _jobs(book)
    return {"book": book, "title": books.read_meta(book).get("title", book), "etag": book_etag(book), "volumes": result, "jobs": jobs, "warnings": warnings}


def _allocate(book: str, request: dict[str, Any]) -> dict[str, Any]:
    request = dict(request)
    if request.get("mode") not in {"prepare", "append", "new_volume"} or type(request.get("count")) is not int or not 1 <= request["count"] <= 50:
        raise ProductionError("PRODUCTION_RANGE_INVALID", "模式或章数非法（1—50章）", 422)
    _uuid(request.get("client_request_id", ""))
    od = outline.read_outline(book)
    vols = od.get("volumes") or []
    vol_no = request.get("volume_no") or (int(vols[0]["no"]) if vols else 1)
    if type(vol_no) is not int or vol_no < 1:
        raise ProductionError("PRODUCTION_RANGE_INVALID", "卷号必须为正整数", 422)
    reserved = [outline._row_no(row.get("章")) or 0 for row in od["chapter_rows"]]
    for vol in vols:
        if len(vol.get("chapters") or []) == 2:
            reserved.append(int(vol["chapters"][1]))
    max_no = max(reserved + chapters.list_chapters(book) + [0])
    vol = next((v for v in vols if int(v["no"]) == vol_no), None)
    mode = request["mode"]
    if mode == "new_volume":
        vol_no = max([int(v["no"]) for v in vols] + [0]) + 1
        start = max_no + 1
    elif mode == "append":
        if vol is None or len(vol.get("chapters") or []) != 2:
            raise ProductionError("PRODUCTION_VOLUME_MISSING", "本卷尚无范围，请先准备本卷")
        start = int(vol["chapters"][1]) + 1
    else:
        if vol and len(vol.get("chapters") or []) == 2:
            start = int(vol["chapters"][0])
        else:
            start = max_no + 1
    numbers = list(range(start, start + request["count"]))
    for other in vols:
        rng = other.get("chapters") or []
        if int(other["no"]) != vol_no and len(rng) == 2 and numbers[0] <= rng[1] and numbers[-1] >= rng[0]:
            raise ProductionError("PRODUCTION_VOLUME_OVERLAP", "追加范围与后卷冲突；请新增下一卷，不自动挪动章号")
    if set(numbers) & set(planning.protected_numbers(book)):
        raise ProductionError("PRODUCTION_PROTECTED", "目标范围含已写正文或锁定细纲，不能覆盖；请选择追加")
    request.update(volume_no=vol_no, numbers=numbers)
    return request


def create(book: str, request: dict[str, Any], expected: str | None, *, client: Any = None, background: bool = True) -> dict[str, Any]:
    with operations.writing(book), _GUARD:
        jobs, warnings = _jobs(book)
        if warnings:
            raise ProductionError("PRODUCTION_INVALID", "存在不可读取任务，请先修复", 503)
        rid = _uuid(request.get("client_request_id", ""))
        for existing in jobs:
            if existing["request"]["client_request_id"] == rid:
                original = {key: existing["request"].get(key) for key in request}
                if digest(original) != digest(request):
                    raise ProductionError("PRODUCTION_IDEMPOTENCY_CONFLICT", "同一请求标识不能更换参数")
                return existing
        check_version(expected, book_etag(book))
        if any(job["status"] not in TERMINAL for job in jobs):
            raise ProductionError("PRODUCTION_BUSY", "本书已有未完成生产任务，请先继续、审核或取消")
        conflicts.guard(book)
        allocated = _allocate(book, request)
        job = {"schema_version": 1, "id": str(uuid4()), "book": book, "request": allocated, "numbers": allocated["numbers"],
               "status": "preparing", "phase": "queued", "current_chapter": None, "completed": [], "applied": [], "reason": "",
               "created_at": db.now(), "updated_at": db.now(), "dependencies": dependencies(book), "input_digest": "",
               "checkpoints": {}, "action_requests": {}, "recovery": {}, "preview_digest": "", "pending_control": ""}
        job["confirmed_outlines"] = {}
        job["boundary_sources"] = {}
        if job["numbers"][0] > 1:
            previous_no = job["numbers"][0] - 1
            for path in (chapters.prose_path(book, previous_no), chapters.outline_path(book, previous_no)):
                job["boundary_sources"][path.relative_to(_root(book)).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        job["input_digest"] = digest(job["dependencies"])
        _save(book, job)
        _ACTIVE.add(_key(book, job["id"]))
    _launch(book, job["id"], client, background)
    return get(book, job["id"])


def _launch(book: str, job_id: str, client: Any, background: bool) -> None:
    if background:
        threading.Thread(target=_run, args=(book, job_id, client), daemon=True, name=f"volume-{job_id[:8]}").start()
    else:
        _run(book, job_id, client)


def _progress(book: str, job_id: str, phase: str, no: int | None) -> None:
    with _GUARD:
        job = _load(book, job_id)
        control = _CONTROLS.get(_key(book, job_id)) or job.get("pending_control")
        if control:
            job.update(status="cancelled" if control == "cancel" else "paused", reason="已在安全检查点停止；已发请求仍可能产生费用", pending_control="")
            _save(book, job)
            raise Paused
        job.update(phase=phase, current_chapter=no)
        _save(book, job)
    conflicts.guard(book)


def _run(book: str, job_id: str, client: Any) -> None:
    from . import volumeproduction
    try:
        with operations.writing(book):
            job = _load(book, job_id)
            if job["dependencies"] != dependencies(book):
                raise ProductionError("PRODUCTION_STALE", "输入已变化，原候选/稿件保留")
            # 每次真正调用模型前检查暂停；不承诺撤销已发送的请求。
            from . import llm
            class TaskClient:
                def chat(self, **kwargs: Any) -> Any:
                    _progress(book, job_id, "model", job.get("current_chapter"))
                    # （规格13 §2.3）不同档位可能走不同端点：按 provider 选真实连接，
                    # 并让它进缓存键，避免换了端点却命中旧检查点。
                    provider = str(kwargs.pop("provider", "") or "")
                    cache_key = digest([job["input_digest"], provider, {key: value for key, value in kwargs.items() if key != "stream_cb"}])
                    cache_file = directory(book, job_id) / "checkpoints" / "prepared_requests" / f"{cache_key}.json"
                    preparing = not job.get("confirmation_digest")
                    if preparing and cache_file.exists():
                        cached = json.loads(cache_file.read_text(encoding="utf-8"))
                        if cached.get("key") != cache_key or not isinstance(cached.get("text"), str) or not isinstance(cached.get("finish_reason"), str):
                            raise ProductionError("PRODUCTION_CHECKPOINT_INVALID", "准备检查点损坏，不自动重发模型请求", 503)
                        result = llm.LLMResult(text=cached["text"], input_tokens=0, output_tokens=0,
                                               finish_reason=cached["finish_reason"],
                                               model=kwargs["model"], provider=provider)
                        result.cached_response = cache_key
                        return result
                    result = (client or llm.client(provider)).chat(**kwargs)
                    if preparing:
                        atomic.write_text(cache_file, json.dumps({"key": cache_key, "text": result.text, "finish_reason": result.finish_reason}, ensure_ascii=False))
                    return result
            task_client = TaskClient()
            if not job.get("confirmation_digest"):
                bundle = volumeproduction.prepare(book, job["request"], client=task_client,
                                                  progress=lambda phase, no=None: _progress(book, job_id, phase, no))
                volumeproduction.validate_bundle(book, bundle, job["numbers"])
                atomic.write_text(directory(book, job_id) / "planning_candidate.json", json.dumps(bundle, ensure_ascii=False, indent=2))
                with _GUARD:
                    job = _load(book, job_id)
                    job.update(status="awaiting_outline_confirmation", phase="prepared", reason="请通读整卷细纲后一次确认", preview_digest=digest([job["input_digest"], bundle]))
                    _save(book, job)
            else:
                for no in job["numbers"]:
                    _progress(book, job_id, "chapter", no)
                    with _GUARD:
                        job = _load(book, job_id)
                    if _source_stale(book, job):
                        raise ProductionError("PRODUCTION_STALE", "前文或依赖已改变，后续待复核")
                    if no in job["completed"]:
                        continue
                    checkpoint = volumeproduction.generate_chapter(book, no, directory(book, job_id), client=task_client)
                    if checkpoint.get("status") != "pending_finalize":
                        raise ProductionError("PRODUCTION_REVIEW_REQUIRED", "本章尚未通过检查，后续已暂停")
                    checkpoint["fingerprint"] = hashlib.sha256(chapters.read_prose(book, no)["_body"].encode("utf-8")).hexdigest()
                    checkpoint["outline_fingerprint"] = hashlib.sha256(chapters.outline_path(book, no).read_bytes()).hexdigest()
                    with _GUARD:
                        job = _load(book, job_id)
                        job["completed"].append(no)
                        job["checkpoints"][str(no)] = checkpoint
                        _save(book, job)
                    atomic.write_text(directory(book, job_id) / "checkpoints" / f"{no:03d}.json", json.dumps(checkpoint, ensure_ascii=False, indent=2))
                _progress(book, job_id, "review", None)
                with _GUARD:
                    job = _load(book, job_id)
                    job.update(status="awaiting_final_review", reason="整卷待审稿已生成；尚未定稿")
                    _save(book, job)
    except Paused:
        pass
    except Exception as exc:
        with _GUARD:
            job = _load(book, job_id)
            job.update(status="stale" if isinstance(exc, ProductionError) and exc.code == "PRODUCTION_STALE" else "paused",
                       reason=str(exc) if isinstance(exc, ProductionError) else f"当前阶段需处理（{type(exc).__name__}）；已完成稿件保留，请查看本章检查后继续")
            _save(book, job)
    finally:
        with _GUARD:
            _ACTIVE.discard(_key(book, job_id))
            _CONTROLS.pop(_key(book, job_id), None)


def _bundle(book: str, job_id: str) -> dict[str, Any] | None:
    try:
        value = _read(directory(book, job_id) / "planning_candidate.json")
        return json.loads(value) if value is not None else None
    except (ValueError, OSError):
        raise ProductionError("PRODUCTION_INVALID", "候选文件损坏，不能确认", 503) from None


def review(book: str, job_id: str) -> dict[str, Any]:
    from . import volumeproduction
    public = get(book, job_id)
    job = _load(book, job_id)
    bundle = _bundle(book, job_id)
    files = []
    if bundle and not job.get("confirmation_digest") and public["status"] == "awaiting_outline_confirmation":
        for name, after in volumeproduction.validate_bundle(book, bundle, job["numbers"]).items():
            files.append({"path": name, "before": _read(_root(book) / name) or "", "after": after})
    items = []
    for no in job["numbers"]:
        path = chapters.prose_path(book, no)
        if not path.is_file():
            continue
        prose = chapters.read_prose(book, no)
        checkpoint = job["checkpoints"].get(str(no), {})
        items.append({"no": no, "title": (outline.chapter_row(book, no) or {}).get("本章目标", f"第{no}章"), "body": prose["_body"],
                      "fingerprint": hashlib.sha256(prose["_body"].encode("utf-8")).hexdigest(),
                      "outline_fingerprint": hashlib.sha256(chapters.outline_path(book, no).read_bytes()).hexdigest(),
                      "status": statemachine.current_status(book, no), "check": checkpoint.get("check"), "review": checkpoint.get("review")})
    return {"job": public, "candidate": bundle, "preview_digest": job["preview_digest"], "files": files, "chapters": items, "warnings": [job["reason"]] if job["status"] in {"stale", "recovery_required", "interrupted"} else []}


def update_candidate(book: str, job_id: str, bundle: dict[str, Any], expected: str | None) -> dict[str, Any]:
    from . import volumeproduction
    with operations.writing(book), _GUARD:
        job = _load(book, job_id)
        check_version(expected, _etag(job))
        if job["status"] != "awaiting_outline_confirmation" or job.get("confirmation_digest"):
            raise ProductionError("PRODUCTION_STATE", "只有待确认候选可以编辑")
        if _source_stale(book, job):
            raise ProductionError("PRODUCTION_STALE", "源档案已变化，请重新准备候选")
        volumeproduction.validate_bundle(book, bundle, job["numbers"])
        atomic.write_text(directory(book, job_id) / "planning_candidate.json", json.dumps(bundle, ensure_ascii=False, indent=2))
        job["preview_digest"] = digest([job["input_digest"], bundle])
        _save(book, job)
    return review(book, job_id)


def _safe_file(book: str, name: str) -> Path:
    root = _root(book)
    if not isinstance(name, str) or "\\" in name or not re.fullmatch(r"(?:OUTLINE|STORY_BIBLE|PLOT_POINTS|LOCATIONS)\.md|characters/char-[^/]+\.md|chapters/[0-9]+_细纲\.md", name):
        raise ProductionError("PRODUCTION_PATH_UNSAFE", "候选包含越权档案路径", 422)
    path = root / name
    if not path.resolve().is_relative_to(root):
        raise ProductionError("PRODUCTION_PATH_UNSAFE", "候选档案路径越界", 422)
    return path


def _rollback(book: str, job: dict[str, Any]) -> None:
    recovery = job["recovery"]
    for name, entry in recovery["files"].items():
        live = _read(_safe_file(book, name))
        if live not in (entry["before"], entry["after"], entry["intermediate"]):
            job.update(status="recovery_required", reason="确认提交期间档案被其他操作改变，禁止自动覆盖")
            _save(book, job)
            raise ProductionError("PRODUCTION_RECOVERY_REQUIRED", job["reason"])
    for name, entry in recovery["files"].items():
        path = _safe_file(book, name)
        if entry["before"] is None:
            if path.exists():
                path.unlink()
        else:
            atomic.write_text(path, entry["before"])
    with db.tx() as conn:
        for no, row in recovery["rows"].items():
            conn.execute("DELETE FROM chapters WHERE book_id=? AND no=?", (books.book_id(book), int(no)))
            if row:
                columns = list(row)
                conn.execute(f"INSERT INTO chapters ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(row[key] for key in columns))
    job.update(recovery={}, status="awaiting_outline_confirmation", reason="确认提交未完成，已恢复原档案；请重新核对")
    _save(book, job)


def recover(book: str, job_id: str) -> dict[str, Any]:
    """（规格10 §5）仅恢复本次确认涉及的原文件/索引，不调用模型。"""
    with operations.writing(book), _GUARD:
        job = _load(book, job_id)
        if job["recovery"]:
            _rollback(book, job)
        for key, receipt in job["action_requests"].items():
            if key.startswith("finalize:") and isinstance(receipt, dict) and receipt.get("state") == "running":
                _recover_final_receipt(book, job, receipt)
    return get(book, job_id)


def confirm(book: str, job_id: str, preview_digest: str, rid: str, expected: str | None, *, client: Any = None, background: bool = True) -> dict[str, Any]:
    from . import pipeline, volumeproduction
    _uuid(rid)
    with operations.writing(book), _GUARD:
        job = _load(book, job_id)
        action = "confirm:" + rid
        if action in job["action_requests"]:
            if job["action_requests"][action] != preview_digest:
                raise ProductionError("PRODUCTION_IDEMPOTENCY_CONFLICT", "确认标识对应的候选不能改变")
            return _public(job)
        check_version(expected, _etag(job))
        if job["status"] != "awaiting_outline_confirmation" or job.get("confirmation_digest"):
            raise ProductionError("PRODUCTION_STATE", "当前不能确认细纲")
        if _source_stale(book, job):
            raise ProductionError("PRODUCTION_STALE", "输入档案已经改变，未采纳候选")
        conflicts.guard(book)
        bundle = _bundle(book, job_id)
        if bundle is None or digest([job["input_digest"], bundle]) != preview_digest or preview_digest != job["preview_digest"]:
            raise ProductionError("PRODUCTION_PREVIEW_CHANGED", "预览已变化，必须重新查看候选")
        files = volumeproduction.validate_bundle(book, bundle, job["numbers"])
        entries = {}
        for name, content in files.items():
            path = _safe_file(book, name)
            after = content
            if name.startswith("chapters/"):
                fm, body = FM.parse(content)
                FM.validate(fm, chapters.OUTLINE_CONTRACT_SCHEMA)
                fm["confirmed"] = True
                after = FM.dump(fm, body)
            entries[name] = {"before": _read(path), "after": after, "intermediate": content}
        job["recovery"] = {"files": entries, "rows": {str(no): statemachine.get_row(book, no) for no in job["numbers"]}}
        job.update(status="recovery_required", reason="正在发布人工确认的整卷档案")
        _save(book, job)
        recovery_snapshot = copy.deepcopy(job["recovery"])
        try:
            for name, entry in entries.items():
                if _read(_safe_file(book, name)) != entry["before"]:
                    raise ProductionError("PRODUCTION_RECOVERY_REQUIRED", "发布期间档案已被其他操作改变，禁止覆盖")
                atomic.write_text(_safe_file(book, name), entry["intermediate"])
            for no in job["numbers"]:
                statemachine.ensure_chapter_row(book, no)
                if statemachine.current_status(book, no) not in {"awaiting_outline", "outline_draft"}:
                    raise ProductionError("PRODUCTION_PROTECTED", "本章状态不允许发布新细纲")
                statemachine.transition(book, no, "generate_outline")
                pipeline.confirm_outline(book, no)
            for name, entry in entries.items():
                if _read(_safe_file(book, name)) != entry["after"]:
                    raise ProductionError("PRODUCTION_WRITE_MISMATCH", "确认文件读回不一致")
            job.update(recovery={}, confirmation_digest=preview_digest, dependencies=dependencies(book), status="generating", phase="queued", reason="", pending_control="")
            job["confirmed_outlines"] = {str(no): hashlib.sha256(chapters.outline_path(book, no).read_bytes()).hexdigest() for no in job["numbers"]}
            job["action_requests"][action] = preview_digest
            _save(book, job)
        except Exception:
            job["recovery"] = recovery_snapshot
            job.pop("confirmation_digest", None)
            job["confirmed_outlines"] = {}
            job["action_requests"].pop(action, None)
            job["dependencies"] = recovery_snapshot.get("dependencies", job["dependencies"])
            _rollback(book, job)
            job["dependencies"] = dependencies(book)
            _save(book, job)
            raise
        _ACTIVE.add(_key(book, job_id))
    _launch(book, job_id, client, background)
    return get(book, job_id)


def control(book: str, job_id: str, action: str, expected: str | None, *, rid: str | None = None, client: Any = None, background: bool = True) -> dict[str, Any]:
    if action not in {"pause", "cancel", "resume"}:
        raise ProductionError("PRODUCTION_ACTION_INVALID", "未知任务动作", 422)
    with _GUARD:
        job = _load(book, job_id)
        if action == "resume":
            _uuid(rid or "")
            key = "resume:" + str(rid)
            if key in job["action_requests"]:
                return _public(job)
        check_version(expected, _etag(job))
        if _key(book, job_id) in _ACTIVE and job["phase"] == "finalizing":
            raise ProductionError("PRODUCTION_BUSY", "人工定稿提交中，请等待当前回执")
        if job["recovery"]:
            raise ProductionError("PRODUCTION_RECOVERY_REQUIRED", "确认提交需先恢复，不能调模型")
        if action in {"pause", "cancel"}:
            if job["status"] in {"applied", "cancelled"}:
                raise ProductionError("PRODUCTION_STATE", "任务已结束")
            if _key(book, job_id) in _ACTIVE:
                _CONTROLS[_key(book, job_id)] = action
                job["pending_control"] = action
                job["reason"] = "停止请求已登记，等待安全检查点；已发模型调用可能继续"
            else:
                job.update(status="cancelled" if action == "cancel" else "paused", pending_control="", reason="已停止，生成内容保留")
            _save(book, job)
            return _public(job)
        if job["status"] not in {"paused", "interrupted", "error"} or _key(book, job_id) in _ACTIVE:
            raise ProductionError("PRODUCTION_STATE", "当前状态不可恢复")
        if _source_stale(book, job):
            raise ProductionError("PRODUCTION_STALE", "前文或输入已变化，必须重新审核受影响内容")
        conflicts.guard(book)
        job.update(status="generating" if job.get("confirmation_digest") else "preparing", pending_control="", reason="")
        job["action_requests"][key] = job["input_digest"]
        _save(book, job)
        _ACTIVE.add(_key(book, job_id))
    _launch(book, job_id, client, background)
    return get(book, job_id)


def _recover_final_receipt(book: str, job: dict[str, Any], receipt: dict[str, Any]) -> None:
    """（规格10 §5）核对已应用正文与不确定回执，禁止自动重调V4。"""
    no = receipt.get("inflight")
    if type(no) is int and no not in {item["no"] for item in receipt["results"]}:
        try:
            prose = chapters.read_prose(book, no)
            applied = (prose.get("status") == "final" and statemachine.current_status(book, no) == "finalized"
                       and hashlib.sha256(prose["_body"].encode("utf-8")).hexdigest() == job["checkpoints"][str(no)]["fingerprint"])
        except (OSError, FM.FrontmatterError):
            applied = False
        receipt["results"].append({"no": no, "applied": applied, "error": "定稿回执中断：已应用状态已核对，记忆回执需人工核对/补做；不会自动重调模型"})
        if applied and no not in job["applied"]:
            job["applied"].append(no)
    receipt.update(state="complete", stopped=True, inflight=None)
    job.update(status="partially_applied", phase="review", reason="已恢复中断回执，请核对本章及正式记忆后继续")
    # 仅承认此次人工提交可能更新的正式记忆；其他输入变化仍会在get/下一次提交标过期。
    current = dependencies(book)
    for key in set(current) | set(job["dependencies"]):
        if key.startswith(("book/characters/", "book/STATE", "book/PLOT_POINTS", "book/LOCATIONS", "book/TIMELINE")):
            if key in current:
                job["dependencies"][key] = current[key]
            else:
                job["dependencies"].pop(key, None)
    _save(book, job)


def finalize(book: str, job_id: str, numbers: list[int], fingerprints: dict[str, str], rid: str, expected: str | None, *, client: Any = None) -> dict[str, Any]:
    """（规格10 §2.4/§5）用户请求顺序定稿；模型等待期间不持全局manifest锁。"""
    from . import pipeline
    _uuid(rid)
    action, request_digest = "finalize:" + rid, digest([numbers, fingerprints])
    with operations.writing(book):
        with _GUARD:
            job = _load(book, job_id)
            if action in job["action_requests"]:
                saved = job["action_requests"][action]
                if saved["digest"] != request_digest:
                    raise ProductionError("PRODUCTION_IDEMPOTENCY_CONFLICT", "定稿标识不能更换章段")
                if saved.get("state") == "running":
                    _recover_final_receipt(book, job, saved)
                return {"job": _public(job), "results": saved["results"], "stopped": saved["stopped"]}
            check_version(expected, _etag(job))
            if job["status"] not in {"awaiting_final_review", "partially_applied"}:
                raise ProductionError("PRODUCTION_STATE", "生成未完成或源稿待复核，不能批量定稿")
            if not numbers or any(type(no) is not int for no in numbers) or numbers != list(range(numbers[0], numbers[-1] + 1)) or not set(numbers).issubset(job["completed"]):
                raise ProductionError("PRODUCTION_RANGE_INVALID", "请选择已完成且通读的连续章段", 422)
            if _source_stale(book, job):
                raise ProductionError("PRODUCTION_STALE", "输入或正文已改动，请重新审核")
            if numbers[0] != min(set(job["numbers"]) - set(job["applied"]), default=numbers[0]):
                raise ProductionError("PRODUCTION_ORDER", "请从尚未定稿的最早章节依次审核")
            for no in numbers:
                if fingerprints.get(str(no)) != job["checkpoints"][str(no)]["fingerprint"]:
                    raise ProductionError("PRODUCTION_STALE", "正文指纹不匹配，拒绝旧稿定稿")
            receipt = {"digest": request_digest, "state": "running", "results": [], "stopped": False, "inflight": None}
            job["action_requests"][action] = receipt
            job["phase"] = "finalizing"
            _save(book, job)
            _ACTIVE.add(_key(book, job_id))
        try:
            for no in numbers:
                with _GUARD:
                    receipt["inflight"] = no
                    job["current_chapter"] = no
                    _save(book, job)
                try:
                    conflicts.guard(book)
                    result = pipeline.finalize(book, no, client=client)
                    applied = statemachine.current_status(book, no) == "finalized"
                    item = {"no": no, "applied": applied, "result": result}
                    if not applied or (result.get("writeback") or {}).get("error") or conflicts.list_open(book):
                        item["error"] = "定稿或记忆回写需处理，后续未继续"
                        receipt["stopped"] = True
                except Exception as exc:
                    item = {"no": no, "applied": statemachine.current_status(book, no) == "finalized", "error": f"定稿需处理（{type(exc).__name__}）"}
                    receipt["stopped"] = True
                with _GUARD:
                    if item["applied"] and no not in job["applied"]:
                        job["applied"].append(no)
                    receipt["results"].append(item)
                    receipt["inflight"] = None
                    job["dependencies"] = dependencies(book)
                    job["status"] = "applied" if len(job["applied"]) == len(job["numbers"]) else "partially_applied"
                    _save(book, job)
                if receipt["stopped"]:
                    break
            with _GUARD:
                receipt["state"] = "complete"
                job.update(phase="review", reason="本次人工审核提交完成" if not receipt["stopped"] else "顺序提交已停止，请核对逐项回执")
                _save(book, job)
                return {"job": _public(job), "results": receipt["results"], "stopped": receipt["stopped"]}
        finally:
            with _GUARD:
                _ACTIVE.discard(_key(book, job_id))
