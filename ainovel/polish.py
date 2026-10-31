"""（规格06 §7—§9、§13）定点润色候选；文件为真相，人工采纳及可解释恢复。"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from . import atomic, books, chapters, db, frontmatter as FM, mechcheck, operations, prompts
from . import paths, settings, statemachine, style

_STATUSES = {"generating", "ready", "rejected", "no_change", "error", "interrupted",
             "applying", "applied", "discarded", "stale", "recovery_required"}
_DIMENSIONS = {"events", "character_decisions", "knowledge", "settings_numbers_costs",
               "time_space_relations", "foreshadow_ending"}
_EDIT_KEYS = {"start_block", "end_block", "original", "replacement", "category", "reason"}
_REQUEST_LOCK = threading.RLock()
_ACTIVE: set[str] = set()
_ROW_KEYS = {"title", "status", "prev_status", "fix_rounds", "current_version", "state_revision",
             "checked_fingerprint", "checked_status", "imported", "updated_at"}
_MANIFEST_TYPES = {
    "schema_version": int, "candidate_id": str, "book": str, "chapter_no": int,
    "client_request_id": str, "request": dict, "created_at": str, "updated_at": str,
    "status": str, "source_revision": int, "source_version": int, "source_state": str,
    "source_fingerprint": str, "source_file_fingerprint": str, "candidate_fingerprint": str,
    "candidate_file_fingerprint": str, "dependencies": dict, "templates": list, "edits": list,
    "checks": dict, "v3": dict, "cost": dict, "warnings": list, "reason_code": str,
    "reason": str, "receipt": dict, "recovery": dict,
}


class PolishError(RuntimeError):
    """（规格06 §10）可由 API 稳定映射的候选错误。"""

    def __init__(self, code: str, reason: str, http_status: int = 409) -> None:
        super().__init__(reason)
        self.code = code
        self.http_status = http_status


def _sha(text: str | bytes) -> str:
    return hashlib.sha256(text if isinstance(text, bytes) else text.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _uuid(value: Any, *, candidate: bool = False) -> str:
    try:
        parsed = uuid.UUID(value) if isinstance(value, str) else None
        if parsed is None or str(parsed) != value or (candidate and parsed.version != 4):
            raise ValueError
    except (ValueError, AttributeError):
        raise PolishError("INVALID_CANDIDATE_ID" if candidate else "INVALID_REQUEST_ID",
                          "标识必须为规范的小写 UUID", 422) from None
    return value


def _contained(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise PolishError("UNSAFE_CANDIDATE_PATH", "候选路径越过受控目录", 409)
    return resolved


def _root(book: str) -> Path:
    root = books.book_dir(book).resolve()
    return _contained(root / "chapters" / "revisions", root)


def _directory(book: str, candidate_id: str) -> Path:
    return _contained(_root(book) / _uuid(candidate_id, candidate=True), _root(book))


def _file(book: str, candidate_id: str, name: str) -> Path:
    directory = _directory(book, candidate_id)
    return _contained(directory / name, directory)


def blocks(body: str) -> list[dict[str, Any]]:
    """（规格06 §7.1）按空行分块；位置仅在服务端使用，分隔符始终留在原稿。"""
    if not isinstance(body, str):
        raise PolishError("INVALID_BODY", "正文必须是文本", 422)
    newline = r"(?:\r\n|(?<!\r)\n|\r(?!\n))"
    separator = re.compile(rf"{newline}[ \t]*{newline}(?:[ \t]*{newline})*")
    spans: list[tuple[int, int]] = []
    start = 0
    for match in separator.finditer(body):
        spans.append((start, match.start()))
        start = match.end()
    spans.append((start, len(body)))
    result = []
    for start, end in spans:
        while start < end and body[start] in "\r\n":
            start += 1
        while end > start and body[end - 1] in "\r\n":
            end -= 1
        if body[start:end].strip():
            result.append({"id": f"p{len(result) + 1:04d}", "text": body[start:end],
                           "start": start, "end": end})
    return result


def _public_blocks(body: str) -> list[dict[str, str]]:
    return [{"id": item["id"], "text": item["text"]} for item in blocks(body)]


def _selection(body: str, mode: str, selected: Any) -> list[str]:
    if mode not in ("auto", "selected") or not isinstance(selected, list):
        raise PolishError("INVALID_SELECTION", "mode 必须是 auto/selected，选区必须是块 ID 数组", 422)
    ids = [item["id"] for item in blocks(body)]
    if any(not isinstance(item, str) or item not in ids for item in selected):
        raise PolishError("INVALID_SELECTION", "选区包含不存在的块", 422)
    if mode == "auto":
        if selected:
            raise PolishError("INVALID_SELECTION", "auto 模式不接受选区", 422)
        return []
    indexes = sorted(ids.index(item) for item in selected)
    if not indexes or len(set(indexes)) != len(indexes) or indexes != list(range(indexes[0], indexes[-1] + 1)):
        raise PolishError("INVALID_SELECTION", "请选择非空、无重复的连续原文块", 422)
    return [ids[index] for index in indexes]


def _patch(body: str, payload: Any, mode: str, selected: list[str]) -> tuple[str, list[dict[str, str]]]:
    if not isinstance(payload, dict) or set(payload) != {"edits"} or not isinstance(payload["edits"], list):
        raise PolishError("INVALID_EDITS", "V2 必须返回仅含 edits 数组的 JSON 对象", 200)
    chunks = blocks(body)
    index = {item["id"]: i for i, item in enumerate(chunks)}
    spans = []
    for edit in payload["edits"]:
        if not isinstance(edit, dict) or set(edit) != _EDIT_KEYS or any(type(v) is not str for v in edit.values()):
            raise PolishError("INVALID_EDITS", "补丁字段缺失或类型非法", 200)
        if edit["start_block"] not in index or edit["end_block"] not in index:
            raise PolishError("PATCH_OUT_OF_RANGE", "补丁块不存在", 200)
        first, last = index[edit["start_block"]], index[edit["end_block"]]
        if first > last:
            raise PolishError("PATCH_OUT_OF_RANGE", "补丁范围倒置", 200)
        if mode == "selected" and any(chunks[i]["id"] not in selected for i in range(first, last + 1)):
            raise PolishError("PATCH_OUTSIDE_SELECTION", "补丁越过用户选区", 200)
        start, end = chunks[first]["start"], chunks[last]["end"]
        if body[start:end] != edit["original"]:
            raise PolishError("PATCH_ORIGINAL_MISMATCH", "补丁原文与服务端范围不逐字一致", 200)
        if any(not edit[key].strip() for key in ("replacement", "category", "reason")):
            raise PolishError("EMPTY_REPLACEMENT", "替换文本、类别及依据均不能为空", 200)
        if len(chunks) > 1 and first == 0 and last == len(chunks) - 1:
            raise PolishError("PATCH_FULL_REWRITE", "不能用整章自由重写冒充定点补丁，请缩小范围", 200)
        spans.append((start, end, edit))
    spans.sort(key=lambda entry: entry[0])
    if any(right[0] < left[1] for left, right in zip(spans, spans[1:])):
        raise PolishError("PATCH_OVERLAP", "补丁范围重叠", 200)
    candidate = body
    for start, end, edit in reversed(spans):
        candidate = candidate[:start] + edit["replacement"] + candidate[end:]
    return candidate, [dict(edit) for _, _, edit in spans]


def _decode(text: str) -> Any:
    """（规格06 §7）只允许一个完整 JSON 或完整 JSON 围栏，不提取自由文本中的片段。"""
    text = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*\n(.*)\n```", text, re.DOTALL)
    if match:
        text = match.group(1)

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("JSON 重复键")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise ValueError(f"非法 JSON 常量 {value}")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (ValueError, TypeError) as exc:
        raise PolishError("INVALID_JSON", "模型未返回完整合法 JSON", 200) from exc


def _v3_error(value: Any, source: str, candidate: str, edits: list[dict[str, str]]) -> str:
    if not isinstance(value, dict):
        return "V3 必须是 JSON 对象"
    required = {"fidelity", "style_evidence", "style_verdict", "clean", "verdict", "new_issues"}
    if not required.issubset(value) or type(value.get("clean")) is not bool:
        return "V3 缺少双稿复核字段或 clean 类型非法"
    fidelity = value["fidelity"]
    if not isinstance(fidelity, dict) or set(fidelity) != _DIMENSIONS:
        return "V3 必须逐项覆盖六个保真维度"
    for dimension in fidelity.values():
        if (not isinstance(dimension, dict) or set(dimension) != {"verdict", "reason"}
                or dimension["verdict"] not in ("pass", "fail", "unknown")
                or not isinstance(dimension["reason"], str) or not dimension["reason"].strip()):
            return "V3 保真维度必须有明确结论及理由"
    if value["style_verdict"] not in ("improved", "unchanged", "worse", "unknown"):
        return "V3 读感结论非法"
    if value["verdict"] not in ("pass", "fix", "fail", "unknown") or not isinstance(value["new_issues"], list):
        return "V3 verdict/new_issues 非法"
    for issue in value["new_issues"]:
        if not isinstance(issue, dict) or issue.get("severity") not in ("blocking", "warning"):
            return "V3 问题必须有明确阻断级别"
    evidence = value["style_evidence"]
    if not isinstance(evidence, list) or (value["style_verdict"] == "improved" and not evidence):
        return "V3 改善结论缺少真实修改前后引用"
    for item in evidence:
        if (not isinstance(item, dict) or set(item) != {"before", "after", "reason"}
                or any(not isinstance(v, str) or not v.strip() for v in item.values())):
            return "V3 引用字段非法"
        if (item["before"] == item["after"] or item["before"] not in source or item["after"] not in candidate
                or not any(item["before"] in edit["original"] and item["after"] in edit["replacement"]
                           and edit["original"] != edit["replacement"] for edit in edits)):
            return "V3 引用不是同一真实编辑范围的修改前后文本"
    return ""


def _v3_pass(value: dict[str, Any]) -> bool:
    return (value.get("clean") is True and value.get("verdict") == "pass"
            and value.get("style_verdict") == "improved"
            and all(v["verdict"] == "pass" for v in value.get("fidelity", {}).values())
            and not any(issue["severity"] == "blocking" for issue in value.get("new_issues", [])))


def _check(book: str, no: int, body: str) -> dict[str, Any]:
    from . import pipeline, state

    cfg = books.load_config(book)
    context = state.mech_context(book, no)
    context["timeline_hits"] = pipeline.timeline_validate(book, body)
    return mechcheck.check_full(body, word_range=cfg["chapter_word_range"],
                                whitelist=list(cfg.get("lint_whitelist", [])), context=context).to_dict()


def _checks(book: str, no: int, source: str, candidate: str) -> dict[str, Any]:
    original, replacement = re.sub(r"\s+", "", source), re.sub(r"\s+", "", candidate)
    before, after = len(original), len(replacement)
    punctuation_before = len(re.findall(r"[。！？!?]", original))
    punctuation_after = len(re.findall(r"[。！？!?]", replacement))
    grams = {original[i:i + 4] for i in range(max(0, len(original) - 3))}
    coverage = sum(gram in replacement for gram in grams) / len(grams) if grams else 1.0
    length_ratio = after / before if before else 0.0
    result: dict[str, Any] = {
        "word_delta": {"pass": bool(before and abs(after - before) * 10 <= before), "before": before, "after": after},
        "length_ratio": {"pass": 0.55 <= length_ratio <= 1.9, "value": length_ratio},
        "punctuation": {"pass": punctuation_after >= punctuation_before * 0.6,
                        "before": punctuation_before, "after": punctuation_after},
        "fourgram": {"pass": coverage >= 0.30, "value": coverage},
        "mechanical": _check(book, no, candidate),
    }
    result["pass"] = (all(result[key]["pass"] for key in ("word_delta", "length_ratio", "punctuation", "fourgram"))
                      and result["mechanical"]["verdict"] == "pass")
    return result


def _file_digest(path: Path) -> str:
    return _sha(path.read_bytes()) if path.is_file() else _sha("（文件缺失）")


def _dependencies(book: str, no: int) -> dict[str, str]:
    """（规格06 §8.1）事实、合同、规则、有效文风及上下文完整内容指纹，不用 mtime。"""
    root = books.book_dir(book)
    facts = {name: _file_digest(root / name) for name in
             ("STORY_BIBLE.md", "OUTLINE.md", "PLOT_POINTS.md", "LOCATIONS.md", "TIMELINE.md",
              "CURRENT_STATE.md", "STATE.md", "SUMMARY.md")}
    for path in sorted((root / "characters").glob("*.md")):
        facts[path.relative_to(root).as_posix()] = _file_digest(path)
    for path in sorted((root / "chapters").glob("*.md")):
        match = re.fullmatch(r"([0-9]+)_(记忆卡|细纲|正文)\.md", path.name)
        if match and (int(match[1]) < no or (int(match[1]) == no and match[2] != "正文")):
            facts[path.relative_to(root).as_posix()] = _file_digest(path)
    cfg = books.load_config(book)
    effective_cfg = {key: cfg.get(key) for key in ("genre_template", "chapter_word_range", "lint_whitelist")}
    rule_files = {p.name: _file_digest(p) for p in sorted(paths.lint_dir().glob("*")) if p.is_file()}
    rule_files["thresholds"] = settings.load().get("mechcheck", {})
    style_data: dict[str, Any] = {"enabled": bool(cfg.get("style_enabled", False))}
    if style_data["enabled"]:
        style_data["context"] = style.context(book, "polish")
        style_data["files"] = {name: _file_digest(root / "style" / name)
                               for name in ("特征卡.md", "语料.txt", "范例.json")}
    template_data = {key: {"digest": value.digest, "source": value.source}
                     for key, value in prompts.library(book).items()}
    return {"facts": _sha(_json(facts)), "contract": _file_digest(chapters.outline_path(book, no)),
            "rules": _sha(_json(rule_files)), "style": _sha(_json(style_data)),
            "configuration": _sha(_json(effective_cfg)), "templates": _sha(_json(template_data))}


def _preflight(book: str) -> None:
    incompatible = []
    for prompt_id in ("V2", "V3"):
        prompt = prompts.get(prompt_id, book)
        if type(prompt.fm.get("interface_version")) is not int or prompt.fm["interface_version"] != 2:
            incompatible.append(f"{prompt_id} ({prompt.source})")
    if incompatible:
        raise PolishError("PROMPT_UPGRADE_REQUIRED", "候选接口需 interface_version=2，请升级：" + "、".join(incompatible))


def _validate_manifest(value: Any) -> None:
    """（规格06 §8.1）严格拒绝未知字段、类型漂移及缺失的恢复数据。"""
    def bad() -> None:
        raise PolishError("INVALID_MANIFEST", "候选 manifest schema 校验失败", 409)

    if not isinstance(value, dict) or set(value) != set(_MANIFEST_TYPES):
        bad()
    for key, expected in _MANIFEST_TYPES.items():
        if type(value[key]) is not expected:
            bad()
    if value["schema_version"] != 1 or value["status"] not in _STATUSES or value["chapter_no"] < 1:
        bad()
    _uuid(value["candidate_id"], candidate=True)
    _uuid(value["client_request_id"])
    if any(not re.fullmatch(r"[0-9a-f]{64}", value[key]) for key in
           ("source_fingerprint", "source_file_fingerprint", "candidate_fingerprint", "candidate_file_fingerprint")):
        bad()
    request = value["request"]
    if (set(request) != {"expected_revision", "mode", "selected_blocks"}
            or request["mode"] not in ("auto", "selected") or type(request["selected_blocks"]) is not list
            or any(type(item) is not str for item in request["selected_blocks"])
            or (request["expected_revision"] is not None and type(request["expected_revision"]) is not int)):
        bad()
    if (set(value["dependencies"]) != {"facts", "contract", "rules", "style", "configuration", "templates"}
            or any(type(v) is not str or not re.fullmatch(r"[0-9a-f]{64}", v) for v in value["dependencies"].values())):
        bad()
    if any(type(w) is not str for w in value["warnings"]):
        bad()
    if (set(value["cost"]) != {"calls", "cost_millicents"}
            or any(type(v) is not int or v < 0 for v in value["cost"].values())):
        bad()
    for edit in value["edits"]:
        if type(edit) is not dict or set(edit) != _EDIT_KEYS or any(type(v) is not str for v in edit.values()):
            bad()
    for template in value["templates"]:
        if (type(template) is not dict or set(template) != {"id", "version", "source", "digest", "request_id", "request_meta"}
                or any(type(template[k]) is not str for k in ("id", "version", "source", "digest", "request_id"))
                or type(template["request_meta"]) is not dict):
            bad()
    recovery = value["recovery"]
    if recovery:
        if (set(recovery) != {"application_id", "row_before", "row_after"}
                or recovery["application_id"] != value["candidate_id"]
                or any(type(recovery[k]) is not dict or set(recovery[k]) != _ROW_KEYS for k in ("row_before", "row_after"))):
            bad()
        for key in ("row_before", "row_after"):
            row = recovery[key]
            if (any(type(row[k]) is not int for k in ("fix_rounds", "current_version", "state_revision", "imported"))
                    or any(type(row[k]) is not str for k in ("title", "status", "updated_at"))
                    or any(row[k] is not None and type(row[k]) is not str
                           for k in ("prev_status", "checked_fingerprint", "checked_status"))):
                bad()
        before, after = recovery["row_before"], recovery["row_after"]
        expected = before | {"status": "mech_check", "state_revision": before["state_revision"] + 2,
                             "checked_status": "stale", "updated_at": after["updated_at"]}
        if (after != expected or before["state_revision"] != value["source_revision"]
                or before["current_version"] != value["source_version"] or before["status"] != value["source_state"]):
            bad()
    if value["status"] in ("applying", "recovery_required") and not recovery:
        bad()
    if value["receipt"]:
        if (set(value["receipt"]) != {"candidate_id", "application_id", "state_revision", "status", "applied_at", "review_id"}
                or value["receipt"]["candidate_id"] != value["candidate_id"]
                or value["receipt"]["application_id"] != value["candidate_id"]
                or type(value["receipt"]["state_revision"]) is not int
                or value["receipt"]["status"] != "mech_check"
                or type(value["receipt"]["applied_at"]) is not str or type(value["receipt"]["review_id"]) is not int):
            bad()
    if value["status"] == "applied" and not value["receipt"]:
        bad()
    if value["status"] in ("ready", "applying", "applied"):
        if value["checks"].get("pass") is not True or not value["v3"] or not _v3_pass(value["v3"]):
            bad()
    _json(value)


def _save(manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = db.now()
    _validate_manifest(manifest)
    atomic.write_text(_file(manifest["book"], manifest["candidate_id"], "manifest.json"), _json(manifest) + "\n")


def _load(book: str, no: int, candidate_id: str) -> dict[str, Any]:
    path = _file(book, candidate_id, "manifest.json")
    if not path.is_file():
        raise PolishError("CANDIDATE_NOT_FOUND", "候选不存在或尚未提交", 404)
    try:
        value = _decode(path.read_text(encoding="utf-8"))
        _validate_manifest(value)
    except (ValueError, PolishError) as exc:
        raise PolishError("INVALID_MANIFEST", "候选 manifest 损坏，不能采纳", 409) from exc
    if value["book"] != book or value["chapter_no"] != no or value["candidate_id"] != candidate_id:
        raise PolishError("CANDIDATE_NOT_FOUND", "候选不属于当前书章", 404)
    return value


def _snapshots(manifest: dict[str, Any]) -> tuple[str, str, str, str]:
    raw = []
    bodies = []
    for name, body_key, file_key in (("source.md", "source_fingerprint", "source_file_fingerprint"),
                                     ("candidate.md", "candidate_fingerprint", "candidate_file_fingerprint")):
        try:
            data = _file(manifest["book"], manifest["candidate_id"], name).read_bytes()
            text = data.decode("utf-8")
            fm, body = FM.parse(text)
            FM.validate(fm, chapters.PROSE_SCHEMA)
        except (OSError, UnicodeError, FM.FrontmatterError) as exc:
            raise PolishError("CANDIDATE_CORRUPT", "候选快照缺失或格式非法") from exc
        if (_sha(data) != manifest[file_key] or _sha(body) != manifest[body_key]
                or fm["chapter_no"] != manifest["chapter_no"]):
            raise PolishError("CANDIDATE_CORRUPT", "候选快照内容指纹不符，禁止采纳")
        raw.append(text)
        bodies.append(body)
    return raw[0], raw[1], bodies[0], bodies[1]


def _row(book: str, no: int) -> dict[str, Any] | None:
    value = statemachine.get_row(book, no)
    return {key: value[key] for key in _ROW_KEYS} if value else None


def _stale_reason(manifest: dict[str, Any]) -> str:
    try:
        _, _, source, _ = _snapshots(manifest)
        book, no = manifest["book"], manifest["chapter_no"]
        data = chapters.prose_path(book, no).read_bytes()
        fm, body = FM.parse(data.decode("utf-8"))
        row = _row(book, no)
        if (not row or row["state_revision"] != manifest["source_revision"]
                or row["current_version"] != manifest["source_version"] or row["status"] != manifest["source_state"]
                or row["status"] not in ("pending_finalize", "human_review")
                or fm.get("status") != "draft" or fm.get("version") != manifest["source_version"]
                or _sha(data) != manifest["source_file_fingerprint"] or _sha(body) != _sha(source)):
            return "原稿完整文件、正文、版本、修订号或主状态已改变"
        if _dependencies(book, no) != manifest["dependencies"]:
            return "合同、事实、有效文风或规则依赖已改变"
    except Exception as exc:
        return f"候选基线不可验证：{exc}"
    return ""


def _refresh(manifest: dict[str, Any]) -> None:
    if manifest["status"] == "generating":
        with _REQUEST_LOCK:
            if manifest["candidate_id"] not in _ACTIVE:
                manifest.update(status="interrupted", reason_code="INTERRUPTED",
                                reason="生成已中断，远端调用可能已计费；同请求不自动重发，重试请使用新请求 ID")
                manifest["warnings"].append("新请求可能重复发生费用")
                _save(manifest)
    if manifest["status"] == "ready":
        reason = _stale_reason(manifest)
        if reason:
            manifest.update(status="stale", reason_code="STALE_CANDIDATE", reason=reason)
            _save(manifest)


def _view(manifest: dict[str, Any]) -> dict[str, Any]:
    source = candidate = ""
    warnings = list(manifest["warnings"])
    try:
        _, _, source, candidate = _snapshots(manifest)
    except PolishError as exc:
        if manifest["status"] not in ("interrupted", "generating", "recovery_required", "stale"):
            raise
        warnings.append(str(exc))
    return {"ok": manifest["status"] not in ("error", "interrupted", "recovery_required"),
            "candidate_id": manifest["candidate_id"], "status": manifest["status"],
            "ready": manifest["status"] == "ready", "applied": manifest["status"] == "applied",
            "source_fingerprint": manifest["source_fingerprint"], "candidate_fingerprint": manifest["candidate_fingerprint"],
            "source_revision": manifest["source_revision"],
            "word_count": {"before": chapters.count_words(source), "after": chapters.count_words(candidate)},
            "edits": manifest["edits"], "checks": manifest["checks"], "v3": manifest["v3"],
            "reason_code": manifest["reason_code"], "reason": manifest["reason"],
            "source": source, "candidate": candidate, "blocks": _public_blocks(source),
            "cost": manifest["cost"], "warnings": warnings, "receipt": manifest["receipt"]}


def _manifests(book: str) -> list[Path]:
    return sorted(_root(book).glob("*/manifest.json"))


def _find_request(request_id: str) -> dict[str, Any] | None:
    roots = {p.resolve() for p in paths.books_dir().glob("*") if p.is_dir()}
    roots.update(Path(p).resolve() for p in paths.attached_books().values())
    for root in sorted(roots):
        revision_root = _contained(root / "chapters" / "revisions", root)
        for path in sorted(revision_root.glob("*/manifest.json")):
            _contained(path, revision_root)
            try:
                value = _decode(path.read_text(encoding="utf-8"))
            except (OSError, PolishError):
                raise PolishError("INVALID_MANIFEST", "已有候选档案损坏，无法安全确认请求幂等性") from None
            if isinstance(value, dict) and value.get("client_request_id") == request_id:
                return _load(value["book"], value["chapter_no"], path.parent.name)
    return None


def _variables(book: str, no: int, source: str) -> dict[str, Any]:
    from . import characters, pipeline, timeline

    path = chapters.outline_path(book, no)
    contract = path.read_text(encoding="utf-8") if path.exists() else "（无细纲导入章：章合同缺失；按原稿及现有档案核对，不得伪造合同）"
    character_text = []
    for name in characters.list_characters(book):
        data = characters.read(book, name)
        if name in source or name in contract:
            character_text.append(_json(data))
    return {"原稿全文": source, "原稿分块JSON": _json(_public_blocks(source)),
            "章合同": contract, "角色约束": "\n".join(character_text) or "（无相关角色档案）",
            "知情边界": timeline.constraints_for_chapter(book, no), "检测器命中": pipeline._deslop_brief(source)}


def _model_call(manifest: dict[str, Any], prompt_id: str, variables: dict[str, Any], client: Any) -> Any:
    from . import pipeline

    manifest["cost"]["calls"] += 1
    _save(manifest)
    result, prompt, _ = pipeline._call(manifest["book"], manifest["chapter_no"], "polish", prompt_id, variables, client=client)
    manifest["cost"]["cost_millicents"] += settings.cost_millicents(
        getattr(result, "model", ""), getattr(result, "input_tokens", 0),
        getattr(result, "output_tokens", 0), provider=getattr(result, "provider", ""))
    meta = getattr(result, "request_meta", None) or {}
    safe_meta = {key: meta[key] for key in ("request_digest", "templates", "style", "token_estimates",
                                           "assembly_version", "warnings", "truncations", "fingerprints") if key in meta}
    manifest["templates"].append({"id": prompt_id, "version": str(prompt.fm["version"]),
                                  "source": prompt.source, "digest": prompt.digest,
                                  "request_id": getattr(result, "request_id", None) or "", "request_meta": safe_meta})
    _save(manifest)
    if str(getattr(result, "finish_reason", "")).lower() in ("length", "max_tokens", "max_output_tokens"):
        raise PolishError("OUTPUT_TRUNCATED", f"{prompt_id} 输出被截断，保留原稿", 200)
    if not isinstance(result.text, str) or not result.text.strip():
        raise PolishError("EMPTY_OUTPUT", f"{prompt_id} 返回空结果", 200)
    return _decode(result.text)


def create_candidate(book: str, no: int, *, expected_revision: int | None = None,
                     client_request_id: str | None = None, mode: str = "auto",
                     selected_blocks: list[str] | None = None, client: Any = None) -> dict[str, Any]:
    """（规格06 §7—§8）生成预览，不写正文、FTS、记忆或主状态；旧无参调用也不自动采纳。"""
    if type(no) is not int or no < 1 or (expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0)):
        raise PolishError("INVALID_REQUEST", "章号或预期修订号非法", 422)
    legacy = client_request_id is None
    request_id = _uuid(str(uuid.uuid4()) if legacy else client_request_id)
    selected = [] if selected_blocks is None else selected_blocks
    try:
        with operations.writing(book):
            return _create(book, no, expected_revision, request_id, mode, selected, legacy, client)
    except operations.BusyError as exc:
        raise PolishError("TASK_RUNNING", str(exc)) from exc


def _create(book: str, no: int, revision: int | None, request_id: str, mode: str,
            selected: list[str], legacy: bool, client: Any) -> dict[str, Any]:
    guard_recovery(book, no)
    with _REQUEST_LOCK:
        existing = _find_request(request_id)
        if existing:
            request = existing["request"]
            if (existing["book"] != book or existing["chapter_no"] != no or request["expected_revision"] != revision
                    or request["mode"] != mode or request["selected_blocks"] != selected):
                raise PolishError("IDEMPOTENCY_CONFLICT", "同一请求 ID 的书、章、修订号或选区不同")
            if existing["status"] == "generating" and existing["candidate_id"] in _ACTIVE:
                raise PolishError("TASK_RUNNING", "此请求正在生成，不会重复调用模型")
            _refresh(existing)
            return _view(existing)
        _preflight(book)
        row = _row(book, no)
        if not row or row["status"] not in ("pending_finalize", "human_review"):
            raise PolishError("INVALID_CHAPTER_STATE", "润色需 pending_finalize/human_review；定稿章必须先解锁")
        if revision is not None and revision != row["state_revision"]:
            raise PolishError("REVISION_CONFLICT", "本章修订号已改变，请刷新")
        raw = chapters.prose_path(book, no).read_bytes().decode("utf-8")
        fm, source = FM.parse(raw)
        FM.validate(fm, chapters.PROSE_SCHEMA)
        if fm["status"] != "draft" or fm["chapter_no"] != no or fm["version"] != row["current_version"]:
            raise PolishError("INVALID_CHAPTER_STATE", "正文版本/状态与索引不一致，不能生成候选")
        selected = _selection(source, mode, selected)
        candidate_id = str(uuid.uuid4())
        manifest = {"schema_version": 1, "candidate_id": candidate_id, "book": book, "chapter_no": no,
                    "client_request_id": request_id,
                    "request": {"expected_revision": revision, "mode": mode, "selected_blocks": selected},
                    "created_at": db.now(), "updated_at": db.now(), "status": "generating",
                    "source_revision": row["state_revision"], "source_version": row["current_version"],
                    "source_state": row["status"], "source_fingerprint": _sha(source),
                    "source_file_fingerprint": _sha(raw), "candidate_fingerprint": _sha(source),
                    "candidate_file_fingerprint": _sha(raw), "dependencies": _dependencies(book, no),
                    "templates": [], "edits": [], "checks": {}, "v3": {}, "cost": {"calls": 0, "cost_millicents": 0},
                    "warnings": ["旧客户端未提供请求 ID；已升级为仅预览，请采纳前人工确认"] if legacy else [],
                    "reason_code": "", "reason": "", "receipt": {}, "recovery": {}}
        atomic.write_text(_file(book, candidate_id, "source.md"), raw)
        atomic.write_text(_file(book, candidate_id, "candidate.md"), raw)
        _save(manifest)
        _ACTIVE.add(candidate_id)
    try:
        baseline = _check(book, no, source)
        manifest["checks"] = {"baseline": baseline}
        if any(hit["rule"] == "M-04" for hit in baseline["hits"]):
            manifest.update(status="rejected", reason_code="SOURCE_SENSITIVE", reason="原稿命中 M-04，禁止发送 V2 自动改写，请人工处理")
        elif baseline["verdict"] == "not_effective":
            manifest.update(status="rejected", reason_code="NOT_EFFECTIVE", reason="规则未完整生效，请先修复规则配置")
        else:
            variables = _variables(book, no, source)
            variables["选区"] = _json({"mode": mode, "selected_blocks": selected})
            payload = _model_call(manifest, "V2", variables, client)
            candidate, edits = _patch(source, payload, mode, selected)
            manifest["edits"] = edits
            if candidate == source:
                manifest.update(status="no_change", reason_code="NO_CHANGE", reason="没有必要的有效修改，保留原稿")
            else:
                candidate_fm = fm | {"word_count": chapters.count_words(candidate)}
                candidate_raw = FM.dump(candidate_fm, candidate)
                FM.validate(candidate_fm, chapters.PROSE_SCHEMA)
                atomic.write_text(_file(book, candidate_id, "candidate.md"), candidate_raw)
                manifest.update(candidate_fingerprint=_sha(candidate), candidate_file_fingerprint=_sha(candidate_raw))
                checked = _checks(book, no, source, candidate)
                manifest["checks"] = checked | {"baseline": baseline}
                if not checked["pass"]:
                    code = "NOT_EFFECTIVE" if checked["mechanical"]["verdict"] == "not_effective" else "CHECKS_FAILED"
                    if any(hit["rule"] == "M-04" for hit in checked["mechanical"]["hits"]):
                        code = "CANDIDATE_SENSITIVE"
                    manifest.update(status="rejected", reason_code=code, reason="候选未通过全部程序保真与完整机检；不自动修复，保留原稿")
                else:
                    review_vars = {key: variables[key] for key in ("原稿全文", "章合同", "角色约束", "知情边界")}
                    review_vars.update({"润色后全文": candidate, "改动清单": _json(edits)})
                    v3 = _model_call(manifest, "V3", review_vars, client)
                    error = _v3_error(v3, source, candidate, edits)
                    if error:
                        manifest.update(status="rejected", reason_code="INVALID_V3", reason=error)
                    else:
                        manifest["v3"] = v3
                        if _v3_pass(v3):
                            manifest.update(status="ready", reason_code="READY", reason="程序及双稿复核通过，等待人工采纳")
                        elif v3["style_verdict"] == "unchanged":
                            manifest.update(status="no_change", reason_code="STYLE_UNCHANGED", reason="复核未确认读感改善，保留原稿")
                        else:
                            manifest.update(status="rejected", reason_code="V3_NOT_PASSED", reason="保真/读感未明确通过，保留原稿")
        if manifest["status"] == "ready":
            reason = _stale_reason(manifest)
            if reason:
                manifest.update(status="stale", reason_code="STALE_CANDIDATE", reason=reason)
        _save(manifest)
    except PolishError as exc:
        manifest.update(status="rejected" if exc.http_status == 200 else "error", reason_code=exc.code, reason=str(exc))
        _save(manifest)
    except Exception as exc:
        manifest.update(status="error", reason_code=getattr(exc, "code", "MODEL_ERROR"),
                        reason="候选生成失败，正式正文未改动；调用费用及日志保留")
        _save(manifest)
    finally:
        with _REQUEST_LOCK:
            _ACTIVE.discard(candidate_id)
    return _view(manifest)


@operations.serialized
def list_candidates(book: str, no: int) -> list[dict[str, Any]]:
    """（规格06 §8）仅扫描已提交 manifest，SQLite 删除后仍可读候选。"""
    result = []
    for path in _manifests(book):
        try:
            manifest = _load(book, no, path.parent.name)
            _refresh(manifest)
            result.append(_view(manifest))
        except PolishError as exc:
            if exc.code not in ("CANDIDATE_NOT_FOUND", "INVALID_CANDIDATE_ID"):
                raise
    return result


@operations.serialized
def get_candidate(book: str, no: int, candidate_id: str) -> dict[str, Any]:
    manifest = _load(book, no, candidate_id)
    _refresh(manifest)
    return _view(manifest)


@operations.serialized
def discard(book: str, no: int, candidate_id: str) -> dict[str, Any]:
    """（规格06 §8.2）放弃不影响正式正文或旧检查，不能用于撤销已采纳正文。"""
    manifest = _load(book, no, candidate_id)
    if manifest["status"] in ("applying", "recovery_required"):
        raise PolishError("RECOVERY_REQUIRED", "采纳恢复未完成，不能放弃恢复标记")
    _refresh(manifest)
    if manifest["status"] == "generating":
        raise PolishError("TASK_RUNNING", "生成进行中，不能放弃")
    if manifest["status"] not in ("applied", "discarded"):
        manifest.update(status="discarded", reason_code="DISCARDED", reason="已保留原稿并放弃此候选")
        _save(manifest)
    return _view(manifest)


def _phase(manifest: dict[str, Any], row: dict[str, Any] | None) -> str:
    if row is None:
        return "foreign"
    before, after = manifest["recovery"]["row_before"], manifest["recovery"]["row_after"]
    if row == before:
        return "before"
    if row == after:
        return "after"
    stable = _ROW_KEYS - {"state_revision", "checked_status", "updated_at"}
    if (all(row[key] == before[key] for key in stable)
            and row["state_revision"] == before["state_revision"] + 1
            and row["checked_status"] in (before["checked_status"], "stale")):
        return "written"
    return "foreign"


def _review_ids(conn: Any, manifest: dict[str, Any]) -> list[int]:
    result = []
    for row in conn.execute("SELECT id, scores_json FROM review_records WHERE book_id=? AND chapter_no=? AND kind='polish'",
                            (conn.execute("SELECT id FROM books WHERE name=?", (manifest["book"],)).fetchone()[0],
                             manifest["chapter_no"])).fetchall():
        try:
            scores = json.loads(row["scores_json"] or "{}")
            if isinstance(scores, dict) and scores.get("candidate_id") == manifest["candidate_id"]:
                result.append(row["id"])
        except ValueError:
            continue
    return result


def _set_row(conn: Any, book: str, no: int, row: dict[str, Any]) -> None:
    keys = sorted(_ROW_KEYS)
    conn.execute("UPDATE chapters SET " + ",".join(f"{key}=?" for key in keys) + " WHERE book_id=? AND no=?",
                 tuple(row[key] for key in keys) + (conn.execute("SELECT id FROM books WHERE name=?", (book,)).fetchone()[0], no))


def _finish(manifest: dict[str, Any]) -> None:
    """（规格06 §9.3）不重写正文、不再次收费，幂等完成本章索引与单次应用记录。"""
    book, no = manifest["book"], manifest["chapter_no"]
    if _phase(manifest, _row(book, no)) == "foreign":
        raise PolishError("RECOVERY_REQUIRED", "本章索引出现不能匹配的外部修订")
    after = manifest["recovery"]["row_after"]
    book_id = books.book_id(book)
    with db.tx() as conn:
        _set_row(conn, book, no, after)
        ids = _review_ids(conn, manifest)
        if not ids:
            cursor = conn.execute(
                "INSERT INTO review_records (book_id,chapter_no,version,kind,scores_json,issues_json,verdict,fingerprint,created_at) "
                "VALUES (?,?,?,'polish',?,?,?,?,?)",
                (book_id, no, manifest["source_version"],
                 _json({"candidate_id": manifest["candidate_id"], "application_id": manifest["candidate_id"], "v3": manifest["v3"]}),
                 _json(manifest["edits"]), "pass", mechcheck.fingerprint(_snapshots(manifest)[3]), after["updated_at"]))
            review_id = cursor.lastrowid
        else:
            review_id = ids[0]
    manifest.update(status="applied", reason_code="APPLIED", reason="已人工采纳；正文需重新机检及审校",
                    receipt={"candidate_id": manifest["candidate_id"], "application_id": manifest["candidate_id"],
                             "state_revision": after["state_revision"], "status": "mech_check",
                             "applied_at": after["updated_at"], "review_id": review_id})
    _save(manifest)


def _rollback(manifest: dict[str, Any]) -> None:
    book, no = manifest["book"], manifest["chapter_no"]
    source_raw, _, _, _ = _snapshots(manifest)
    current = _sha(chapters.prose_path(book, no).read_bytes())
    if (current not in (manifest["source_file_fingerprint"], manifest["candidate_file_fingerprint"])
            or _phase(manifest, _row(book, no)) == "foreign"):
        raise PolishError("RECOVERY_REQUIRED", "发现第三种正文或外部修订，不自动覆盖")
    atomic.write_text(chapters.prose_path(book, no), source_raw)
    with db.tx() as conn:
        _set_row(conn, book, no, manifest["recovery"]["row_before"])
        for review_id in _review_ids(conn, manifest):
            conn.execute("DELETE FROM review_records WHERE id=?", (review_id,))
    restored = manifest | {"status": "ready", "receipt": {}, "recovery": {}, "reason_code": "APPLY_ROLLED_BACK",
                           "reason": "采纳异常已回滚正式正文及本章索引，可重新确认采纳"}
    _save(restored)
    manifest.clear()
    manifest.update(restored)


def _require_recovery(manifest: dict[str, Any], reason: str) -> None:
    manifest.update(status="recovery_required", reason_code="RECOVERY_REQUIRED", reason=reason)
    _save(manifest)


@operations.serialized
def guard_recovery(book: str, no: int) -> None:
    """（规格06 §9.3）只恢复可识别的本次采纳；第三正文保持只读，不覆盖外改。"""
    if operations.recovery_writing(book, no):
        return
    for path in _manifests(book):
        try:
            manifest = _load(book, no, path.parent.name)
        except PolishError as exc:
            if exc.code in ("CANDIDATE_NOT_FOUND", "INVALID_CANDIDATE_ID"):
                continue
            raise
        if manifest["status"] == "recovery_required":
            raise PolishError("RECOVERY_REQUIRED", manifest["reason"])
        if manifest["status"] != "applying":
            continue
        try:
            _snapshots(manifest)
            current = _sha(chapters.prose_path(book, no).read_bytes())
            if _phase(manifest, _row(book, no)) == "foreign":
                raise PolishError("RECOVERY_REQUIRED", "索引修订与恢复记录不符，不覆盖外改")
            if current == manifest["source_file_fingerprint"]:
                _rollback(manifest)
            elif current == manifest["candidate_file_fingerprint"]:
                _finish(manifest)
            else:
                raise PolishError("RECOVERY_REQUIRED", "发现第三种正文，保留原稿/候选/当前正文供人工恢复")
        except Exception as exc:
            _require_recovery(manifest, "采纳恢复未完成：" + str(exc))
            raise PolishError("RECOVERY_REQUIRED", manifest["reason"]) from exc


@operations.serialized
def approve(book: str, no: int, candidate_id: str, *, expected_revision: int,
            source_fingerprint: str, candidate_fingerprint: str) -> dict[str, Any]:
    """（规格06 §9）双指纹、完整文件与依赖再校验；只有此入口会写回候选。"""
    guard_recovery(book, no)
    manifest = _load(book, no, candidate_id)
    if (type(expected_revision) is not int or type(source_fingerprint) is not str
            or type(candidate_fingerprint) is not str):
        raise PolishError("INVALID_APPROVAL", "采纳必须提供修订号及双指纹", 422)
    if (expected_revision != manifest["source_revision"] or source_fingerprint != manifest["source_fingerprint"]
            or candidate_fingerprint != manifest["candidate_fingerprint"]):
        raise PolishError("APPROVAL_CONFLICT", "采纳参数与候选基线不符")
    if manifest["status"] == "applied":
        return _view(manifest)
    _refresh(manifest)
    if manifest["status"] != "ready":
        raise PolishError("STALE_CANDIDATE" if manifest["status"] == "stale" else "CANDIDATE_NOT_READY",
                          manifest["reason"] or "候选不可采纳")
    _, _, source, candidate = _snapshots(manifest)
    error = _v3_error(manifest["v3"], source, candidate, manifest["edits"])
    checked = _checks(book, no, source, candidate)
    reconstructed, edits = _patch(source, {"edits": manifest["edits"]}, manifest["request"]["mode"],
                                  manifest["request"]["selected_blocks"])
    if error or not _v3_pass(manifest["v3"]) or not checked["pass"] or reconstructed != candidate or edits != manifest["edits"]:
        manifest.update(status="rejected", reason_code="APPROVAL_CHECK_FAILED", reason=error or "采纳前完整复检/补丁重建未通过")
        _save(manifest)
        raise PolishError("APPROVAL_CHECK_FAILED", manifest["reason"])
    reason = _stale_reason(manifest)
    if reason:
        manifest.update(status="stale", reason_code="STALE_CANDIDATE", reason=reason)
        _save(manifest)
        raise PolishError("STALE_CANDIDATE", reason)
    before = _row(book, no)
    if before is None:
        raise PolishError("STALE_CANDIDATE", "本章索引缺失")
    after = before | {"status": "mech_check", "state_revision": before["state_revision"] + 2,
                      "checked_status": "stale", "updated_at": db.now()}
    manifest.update(status="applying", recovery={"application_id": candidate_id, "row_before": before, "row_after": after})
    _save(manifest)
    try:
        candidate_raw = _snapshots(manifest)[1]
        fm, body = FM.parse(candidate_raw)
        with operations.recovery_write(book, no):
            chapters.write_prose(book, no, fm, body, expected_revision=expected_revision, recheck=False)
        if _sha(chapters.prose_path(book, no).read_bytes()) != manifest["candidate_file_fingerprint"]:
            raise PolishError("RECOVERY_REQUIRED", "写回后的正文与目标候选不一致")
        _finish(manifest)
    except Exception as exc:
        try:
            _rollback(manifest)
        except Exception as recovery_error:
            _require_recovery(manifest, "采纳失败且不能安全回滚：" + str(recovery_error))
            raise PolishError("RECOVERY_REQUIRED", manifest["reason"]) from exc
        raise PolishError("APPLY_FAILED", "采纳失败，正式正文与本章索引已回滚", 500) from exc
    return _view(manifest)
