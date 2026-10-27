"""（规格06 §7—§9、§13）候选、并发、文件真相及故障恢复离线回归。"""
from __future__ import annotations

import copy
import json
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest

from ainovel import atomic, books, chapters, db, frontmatter as FM, llm, mechcheck
from ainovel import operations, pipeline, polish, prompts, statemachine, style

BODY = (
    "林寒推开木门，把工具袋放在长凳上。桌面的铜片已经发黑，他先擦掉油污，再沿裂口量了一遍，没有碰旁边封好的信。\n\n"
    "老周提着灯进屋，把灯挂到窗边。他指了指门外的水沟，说昨夜雨大，东面的墙脚还在渗水，走路要绕开那块松动的石板。\n\n"
    "林寒从袋底找出小锉刀，压住铜片的一端。他锉下去时留着余量，试过两次才把接口合拢，碎屑落进垫在桌边的纸里。\n\n"
    "窗外有人叫卖热饼，声音隔着院墙传来。老周去把门闩插好，回头看了一眼灯芯，取过竹签拨开焦黑的头，屋里亮了一些。\n\n"
    "铜片装回木盒后，林寒握住把手试转。齿轮只响了一声便停住，他记下卡住的位置，没有再使力，把盒盖搁在手边等老周来看。\n\n"
    "两人对着那处磨痕看了许久。老周取出压在柜底的旧图纸，沿边角摊平，林寒挪开灯座，拿铅笔在相同的位置画了个圈。\n"
)


@pytest.fixture
def env(book_env: tuple[str, Path], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    book, home = book_env
    cfg = books.load_config(book)
    cfg["chapter_word_range"] = [100, 1500]
    books.save_config(book, cfg)
    for prompt_id in ("V2", "V3"):
        prompt = prompts.get(prompt_id)
        fm = prompt.fm | {"interface_version": 2}
        atomic.write_text(prompt.path, FM.dump(fm, prompt.body))
    fm = {"chapter_no": 1, "version": 1, "status": "draft", "word_count": chapters.count_words(BODY)}
    chapters.write_prose(book, 1, fm, BODY)
    statemachine.set_status_direct(book, 1, "pending_finalize")
    db.set_check_status(book_id=books.book_id(book), chapter_no=1,
                        fingerprint=mechcheck.fingerprint(BODY), status="exempt")
    calls: list[dict[str, Any]] = []
    control: dict[str, Any] = {}

    def call(book: str, no: int, stage: str, prompt_id: str, variables: dict[str, Any],
             client: Any = None, **kwargs: Any) -> tuple[Any, Any, str]:
        calls.append({"book": book, "no": no, "stage": stage, "id": prompt_id, "vars": copy.deepcopy(variables)})
        if "hook" in control:
            control["hook"](prompt_id, variables)
        if "exception" in control:
            raise control["exception"]
        if prompt_id == "V2":
            chunk = polish.blocks(variables["原稿全文"])[0]
            payload: Any = {"edits": [{"start_block": chunk["id"], "end_block": chunk["id"],
                                       "original": chunk["text"], "replacement": chunk["text"].replace("推开木门", "推开房门"),
                                       "category": "用词", "reason": "改用当前场景中更贴切的称呼"}]}
        else:
            edits = json.loads(variables["改动清单"])
            payload = {"fidelity": {key: {"verdict": "pass", "reason": "对照双稿，事实与结果未变"}
                                    for key in polish._DIMENSIONS},
                       "style_evidence": [{"before": edits[0]["original"], "after": edits[0]["replacement"],
                                           "reason": "用词与场景更贴合"}],
                       "style_verdict": "improved", "clean": True, "verdict": "pass", "new_issues": []}
        payload = copy.deepcopy(control.get(prompt_id, payload))
        if prompt_id == "V3" and "mutate_v3" in control:
            control["mutate_v3"](payload)
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        result = llm.LLMResult(text, 100, 100, finish_reason=control.get("finish", "stop"), model="deepseek-chat")
        result.request_id = str(uuid.uuid4())
        result.request_meta = {"request_digest": "d" * 64, "assembly_version": "2"}
        prompt = prompts.get(prompt_id, book)
        db.log_generation(book_id=books.book_id(book), chapter_no=no, stage=stage, prompt_id=prompt_id,
                          prompt_version=str(prompt.fm["version"]), prompt_digest=prompt.digest, model=result.model,
                          input_tokens=100, output_tokens=100, cost_millicents=100)
        return result, prompt, "测试用离线请求"

    monkeypatch.setattr(pipeline, "_call", call)
    return {"book": book, "home": home, "calls": calls, "control": control,
            "revision": statemachine.get_row(book, 1)["state_revision"]}


def create(env: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("expected_revision", env["revision"])
    kwargs.setdefault("client_request_id", str(uuid.uuid4()))
    return polish.create_candidate(env["book"], 1, **kwargs)


def approve(env: dict[str, Any], value: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    args = {"expected_revision": value["source_revision"], "source_fingerprint": value["source_fingerprint"],
            "candidate_fingerprint": value["candidate_fingerprint"]} | kwargs
    return polish.approve(env["book"], 1, value["candidate_id"], **args)


def application_records(env: dict[str, Any]) -> list[Any]:
    with db.tx() as conn:
        return conn.execute("SELECT * FROM review_records WHERE book_id=? AND chapter_no=1 AND kind='polish'",
                            (books.book_id(env["book"]),)).fetchall()


def edit_for(body: str, first: int = 0, last: int | None = None, replacement: str | None = None) -> dict[str, str]:
    last = first if last is None else last
    items = polish.blocks(body)
    original = body[items[first]["start"]:items[last]["end"]]
    return {"start_block": items[first]["id"], "end_block": items[last]["id"], "original": original,
            "replacement": original + "改" if replacement is None else replacement, "category": "措辞", "reason": "减少重复解释"}


def test_blocks_stable_and_exact_offsets(home: Path) -> None:
    body = "\r\n 甲段。\r\n下一行。  \r\n \t\r\n\r\n乙段。\n\n丙段。\r\n"
    chunks = polish.blocks(body)
    assert chunks == polish.blocks(body)
    assert [item["id"] for item in chunks] == ["p0001", "p0002", "p0003"]
    assert all(body[item["start"]:item["end"]] == item["text"] for item in chunks)
    assert chunks[0]["text"] == " 甲段。\r\n下一行。  "
    assert polish.blocks(" \n\n \t") == []


def test_patch_merge_split_preserves_uninvolved_separators(home: Path) -> None:
    body = "甲段。\r\n \r\n乙段。\n\n丙段。\n\n丁段。\r\n"
    edit = edit_for(body, 1, 2, "合并段。\n\n拆分段。")
    candidate, edits = polish._patch(body, {"edits": [edit]}, "selected", ["p0002", "p0003"])
    assert candidate == "甲段。\r\n \r\n合并段。\n\n拆分段。\n\n丁段。\r\n"
    assert edits == [edit]


@pytest.mark.parametrize("case,code", [("range", "PATCH_OUT_OF_RANGE"), ("reverse", "PATCH_OUT_OF_RANGE"),
    ("overlap", "PATCH_OVERLAP"), ("original", "PATCH_ORIGINAL_MISMATCH"), ("empty", "EMPTY_REPLACEMENT"),
    ("selection", "PATCH_OUTSIDE_SELECTION"), ("full", "PATCH_FULL_REWRITE"), ("extra", "INVALID_EDITS"),
    ("legacy", "INVALID_EDITS"), ("wrong_type", "INVALID_EDITS")])
def test_invalid_patches(home: Path, case: str, code: str) -> None:
    body = "甲段。\n\n乙段。\n\n丙段。"
    edit = edit_for(body)
    payload: Any = {"edits": [edit]}
    mode, selected = "auto", []
    if case == "range":
        edit["end_block"] = "p9999"
    elif case == "reverse":
        edit.update(start_block="p0002", end_block="p0001")
    elif case == "overlap":
        payload["edits"].append(copy.deepcopy(edit))
    elif case == "original":
        edit["original"] += " "
    elif case == "empty":
        edit["replacement"] = " \n"
    elif case == "selection":
        mode, selected = "selected", ["p0002"]
    elif case == "full":
        payload = {"edits": [edit_for(body, 0, 2)]}
    elif case == "extra":
        edit["start"] = 0
    elif case == "legacy":
        payload = {"正文": body}
    else:
        edit["replacement"] = 4
    with pytest.raises(polish.PolishError) as caught:
        polish._patch(body, payload, mode, selected)
    assert caught.value.code == code


@pytest.mark.parametrize("mode,selected", [("selected", []), ("selected", ["p0001", "p0003"]),
    ("selected", ["p0001", "p0001"]), ("selected", ["p9999"]), ("auto", ["p0001"]), ("bad", [])])
def test_invalid_selection_no_calls(env: dict[str, Any], mode: str, selected: list[str]) -> None:
    with pytest.raises(polish.PolishError) as caught:
        create(env, mode=mode, selected_blocks=selected)
    assert caught.value.http_status == 422
    assert not env["calls"]
    assert polish.list_candidates(env["book"], 1) == []


def test_candidate_preview_and_discard_are_side_effect_free(env: dict[str, Any]) -> None:
    book = env["book"]
    before = chapters.prose_path(book, 1).read_bytes()
    row = statemachine.get_row(book, 1)
    value = create(env)
    assert value["status"] == "ready", value
    assert value["ok"] is True and value["applied"] is False
    assert chapters.prose_path(book, 1).read_bytes() == before
    assert statemachine.get_row(book, 1) == row
    assert not application_records(env)
    assert value["cost"]["calls"] == 2
    assert all(set(block) == {"id", "text"} for block in value["blocks"])
    assert env["calls"][1]["vars"]["原稿全文"] == BODY
    assert env["calls"][1]["vars"]["润色后全文"] == value["candidate"]
    assert "合同缺失" in env["calls"][1]["vars"]["章合同"]
    assert all(key in env["calls"][0]["vars"] for key in ("原稿分块JSON", "选区", "角色约束", "知情边界", "检测器命中"))
    assert polish.discard(book, 1, value["candidate_id"])["status"] == "discarded"
    assert polish.discard(book, 1, value["candidate_id"])["status"] == "discarded"
    assert chapters.prose_path(book, 1).read_bytes() == before
    assert statemachine.get_row(book, 1) == row
    assert not application_records(env)


def test_success_uses_write_prose_and_single_receipt(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    value = create(env)
    assert value["ready"], value
    writes = []
    real = chapters.write_prose

    def write(*args: Any, **kwargs: Any) -> None:
        writes.append(kwargs)
        real(*args, **kwargs)

    monkeypatch.setattr(chapters, "write_prose", write)
    first = approve(env, value)
    row = statemachine.get_row(env["book"], 1)
    assert first["applied"] and not first["ready"]
    assert row["status"] == "mech_check" and row["checked_status"] == "stale"
    assert row["state_revision"] == value["source_revision"] + 2
    assert row["current_version"] == 1
    assert chapters.read_prose(env["book"], 1)["_body"] == value["candidate"]
    assert len(writes) == 1 and writes[0]["expected_revision"] == value["source_revision"]
    assert len(application_records(env)) == 1
    assert json.loads(application_records(env)[0]["scores_json"])["candidate_id"] == value["candidate_id"]
    assert approve(env, value)["receipt"] == first["receipt"]
    assert statemachine.get_row(env["book"], 1) == row
    assert len(writes) == 1 and len(env["calls"]) == 2
    assert polish.discard(env["book"], 1, value["candidate_id"])["status"] == "applied"


def test_empty_edits_no_change_preserves_exemption(env: dict[str, Any]) -> None:
    env["control"]["V2"] = {"edits": []}
    row = statemachine.get_row(env["book"], 1)
    value = create(env)
    assert value["status"] == "no_change" and value["candidate"] == BODY
    assert value["edits"] == [] and not value["ready"]
    assert statemachine.get_row(env["book"], 1) == row
    assert len(env["calls"]) == 1
    with pytest.raises(polish.PolishError):
        approve(env, value)


@pytest.mark.parametrize("prompt_id", ["V2", "V3"])
def test_old_book_override_requires_upgrade_before_any_call(env: dict[str, Any], prompt_id: str) -> None:
    prompt = prompts.get(prompt_id)
    fm = dict(prompt.fm)
    fm.pop("interface_version", None)
    override = books.book_dir(env["book"]) / "prompts" / "review" / f"{prompt_id}.md"
    raw = FM.dump(fm, prompt.body)
    atomic.write_text(override, raw)
    with pytest.raises(polish.PolishError) as caught:
        create(env)
    assert caught.value.code == "PROMPT_UPGRADE_REQUIRED" and caught.value.http_status == 409
    assert prompt_id in str(caught.value)
    assert not env["calls"] and override.read_text(encoding="utf-8") == raw


@pytest.mark.parametrize("payload,finish,code", [
    ("===润色全文===\n任意整章改稿\n===改动清单===", "stop", "INVALID_JSON"),
    ('{"edits":[],"edits":[]}', "stop", "INVALID_JSON"),
    ("{} trailing", "stop", "INVALID_JSON"),
    ("", "stop", "EMPTY_OUTPUT"), ({"edits": []}, "length", "OUTPUT_TRUNCATED"),
    ({"edits": []}, "MAX_TOKENS", "OUTPUT_TRUNCATED")])
def test_old_invalid_empty_truncated_output_rejected(env: dict[str, Any], payload: Any, finish: str, code: str) -> None:
    env["control"].update(V2=payload, finish=finish)
    value = create(env)
    assert value["reason_code"] == code and not value["ready"]
    assert chapters.read_prose(env["book"], 1)["_body"] == BODY
    assert len(env["calls"]) == 1


@pytest.mark.parametrize("case", ["unknown", "fail", "missing_dimension", "old", "worse", "unknown_style", "unchanged",
    "string_clean", "not_pass", "invented_quote", "no_evidence", "unchanged_quote", "blocking", "malformed_dimension"])
def test_v3_strict_semantics_and_real_quotes(env: dict[str, Any], case: str) -> None:
    def mutate(value: dict[str, Any]) -> None:
        if case in ("unknown", "fail"):
            value["fidelity"]["knowledge"]["verdict"] = case
        elif case == "missing_dimension":
            value["fidelity"].pop("events")
        elif case == "old":
            value.pop("fidelity")
        elif case in ("worse", "unchanged"):
            value["style_verdict"] = case
        elif case == "unknown_style":
            value["style_verdict"] = "unknown"
        elif case == "string_clean":
            value["clean"] = "true"
        elif case == "not_pass":
            value["verdict"] = "fix"
        elif case == "invented_quote":
            value["style_evidence"][0]["before"] = "原稿里没有的句子"
        elif case == "no_evidence":
            value["style_evidence"] = []
        elif case == "unchanged_quote":
            value["style_evidence"] = [{"before": "工具袋", "after": "工具袋", "reason": "错误自报改善"}]
        elif case == "blocking":
            value["new_issues"] = [{"severity": "blocking", "description": "事件改变"}]
        else:
            value["fidelity"]["events"] = "pass"
    env["control"]["mutate_v3"] = mutate
    result = create(env)
    assert result["status"] == ("no_change" if case == "unchanged" else "rejected"), result
    assert not result["ready"] and chapters.read_prose(env["book"], 1)["_body"] == BODY


def test_sensitive_source_no_v2(env: dict[str, Any]) -> None:
    atomic.write_text(env["home"] / "lint" / "sensitive_words.txt", "工具袋\n")
    value = create(env)
    assert value["reason_code"] == "SOURCE_SENSITIVE" and not env["calls"]
    assert statemachine.get_row(env["book"], 1)["checked_status"] == "exempt"


def test_sensitive_candidate_no_v3_or_autofix(env: dict[str, Any]) -> None:
    atomic.write_text(env["home"] / "lint" / "sensitive_words.txt", "房门\n")
    result = create(env)
    assert result["reason_code"] == "CANDIDATE_SENSITIVE", result
    assert [call["id"] for call in env["calls"]] == ["V2"]


@pytest.mark.parametrize("phase", ["source", "candidate"])
def test_not_effective_with_empty_hits_is_not_pass(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, phase: str) -> None:
    real = polish._check
    count = 0

    def check(book: str, no: int, body: str) -> dict[str, Any]:
        nonlocal count
        count += 1
        value = real(book, no, body)
        if count == (1 if phase == "source" else 2):
            value.update(verdict="not_effective", hits=[], ineffective=[{"rule": "M-04", "reason": "missing_file"}])
        return value
    monkeypatch.setattr(polish, "_check", check)
    value = create(env)
    assert value["reason_code"] == "NOT_EFFECTIVE"
    assert len(env["calls"]) == (0 if phase == "source" else 1)


@pytest.mark.parametrize("source,candidate,failed", [
    ("甲乙丙丁。" * 30, "甲乙丙丁。" * 34, "word_delta"),
    ("甲乙丙丁。" * 30, "甲乙丙丁。" * 10, "length_ratio"),
    ("甲乙丙丁。" * 30, "甲乙丙丁戊" * 30, "punctuation"),
    ("甲乙丙丁。" * 30, "戊己庚辛。" * 30, "fourgram")])
def test_all_literal_fidelity_checks_run(home: Path, monkeypatch: pytest.MonkeyPatch,
                                         source: str, candidate: str, failed: str) -> None:
    calls = []
    def check(*args: Any) -> dict[str, Any]:
        calls.append(args)
        return {"verdict": "pass", "hits": [], "ineffective": []}
    monkeypatch.setattr(polish, "_check", check)
    result = polish._checks("测试书", 1, source, candidate)
    assert not result[failed]["pass"] and not result["pass"]
    assert len(calls) == 1 and result["mechanical"]["verdict"] == "pass"


def test_exact_ten_percent_boundary(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(polish, "_check", lambda *args: {"verdict": "pass"})
    source = "甲乙丙丁。" * 20
    assert polish._checks("书", 1, source, source + "甲乙丙丁。" * 2)["word_delta"]["pass"]
    assert not polish._checks("书", 1, source, source + "甲乙丙丁。" * 2 + "甲")["word_delta"]["pass"]


@pytest.mark.parametrize("change", ["body", "metadata", "crlf", "revision", "version", "state", "finalized",
    "contract", "facts", "character", "timeline", "rules", "style", "thresholds", "config"])
def test_all_stale_dependencies_reject_without_overwrite(env: dict[str, Any], change: str) -> None:
    book = env["book"]
    if change == "style":
        cfg = books.load_config(book)
        cfg["style_enabled"] = True
        books.save_config(book, cfg)
        style.write_card(book, "原有人工特征卡")
    value = create(env)
    assert value["ready"], value
    path = chapters.prose_path(book, 1)
    root = books.book_dir(book)
    if change in ("body", "metadata", "crlf"):
        raw = path.read_bytes().decode("utf-8")
        raw = (raw + "外部手改" if change == "body" else raw.replace("status: draft", "status: draft\nmanual: true")
               if change == "metadata" else raw.replace("\n", "\r\n"))
        atomic.write_text(path, raw)
    elif change == "revision":
        statemachine.bump_revision(book, 1)
    elif change == "version":
        statemachine.set_current_version(book, 1, 2)
    elif change in ("state", "finalized"):
        statemachine.set_status_direct(book, 1, "human_review" if change == "state" else "finalized")
    elif change == "contract":
        atomic.write_text(chapters.outline_path(book, 1), "外部新合同")
    elif change == "facts":
        atomic.write_text(root / "STORY_BIBLE.md", "新设定")
    elif change == "character":
        atomic.write_text(root / "characters" / "新角色.md", "新角色档案")
    elif change == "timeline":
        atomic.write_text(root / "TIMELINE.md", "新知情边界")
    elif change == "rules":
        atomic.write_text(env["home"] / "lint" / "sensitive_words.txt", "新规则\n")
    elif change == "style":
        style.write_card(book, "外部改过的正式卡")
    elif change == "thresholds":
        from ainovel import settings
        cfg = settings.load()
        cfg["mechcheck"]["ai_flavor_warn"] = 12
        settings.save(cfg)
    else:
        cfg = books.load_config(book)
        cfg["chapter_word_range"] = [100, 1000]
        books.save_config(book, cfg)
    current = path.read_bytes()
    with pytest.raises(polish.PolishError) as caught:
        approve(env, value)
    assert caught.value.http_status == 409
    assert chapters.prose_path(book, 1).read_bytes() == current
    assert not application_records(env)
    assert polish.get_candidate(book, 1, value["candidate_id"])["status"] == "stale"


def test_disabled_style_changes_do_not_stale_effective_dependencies(env: dict[str, Any]) -> None:
    value = create(env)
    style.write_card(env["book"], "未启用的新卡")
    style.write_corpus(env["book"], "未启用的新语料")
    assert polish.get_candidate(env["book"], 1, value["candidate_id"])["ready"]


@pytest.mark.parametrize("field", ["expected_revision", "source_fingerprint", "candidate_fingerprint"])
def test_approval_parameters_must_match(env: dict[str, Any], field: str) -> None:
    value = create(env)
    with pytest.raises(polish.PolishError) as caught:
        approve(env, value, **{field: 12345 if field == "expected_revision" else "0" * 64})
    assert caught.value.code == "APPROVAL_CONFLICT"
    assert chapters.read_prose(env["book"], 1)["_body"] == BODY


def test_idempotent_requests_and_cross_book_chapter_conflicts(env: dict[str, Any]) -> None:
    request = str(uuid.uuid4())
    value = create(env, client_request_id=request)
    assert create(env, client_request_id=request)["candidate_id"] == value["candidate_id"]
    assert len(env["calls"]) == 2
    books.create_book("另一本书")
    for book, no, revision, mode, selected in [
        (env["book"], 2, env["revision"], "auto", []),
        ("另一本书", 1, env["revision"], "auto", []),
        (env["book"], 1, 999, "auto", []),
        (env["book"], 1, env["revision"], "selected", ["p0001"])]:
        with pytest.raises(polish.PolishError) as caught:
            polish.create_candidate(book, no, client_request_id=request, expected_revision=revision,
                                    mode=mode, selected_blocks=selected)
        assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    assert len(env["calls"]) == 2


def test_generating_reentrant_request_does_not_charge_again(env: dict[str, Any]) -> None:
    request = str(uuid.uuid4())
    conflicts = []
    def hook(prompt_id: str, variables: Any) -> None:
        if prompt_id == "V2":
            with pytest.raises(polish.PolishError) as caught:
                create(env, client_request_id=request)
            conflicts.append(caught.value.code)
    env["control"]["hook"] = hook
    assert create(env, client_request_id=request)["ready"]
    assert conflicts == ["TASK_RUNNING"] and len(env["calls"]) == 2


def test_concurrent_same_book_is_blocked(env: dict[str, Any]) -> None:
    entered, release = threading.Event(), threading.Event()
    def hold() -> None:
        with operations.writing(env["book"]):
            entered.set()
            release.wait(5)
    thread = threading.Thread(target=hold)
    thread.start()
    assert entered.wait(5)
    try:
        with pytest.raises(polish.PolishError) as caught:
            create(env)
        assert caught.value.code == "TASK_RUNNING"
        assert not env["calls"]
    finally:
        release.set()
        thread.join(5)


def test_interrupted_generation_never_auto_reissues(env: dict[str, Any]) -> None:
    request = str(uuid.uuid4())
    env["control"]["exception"] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        create(env, client_request_id=request)
    env["control"].clear()
    value = create(env, client_request_id=request)
    assert value["status"] == "interrupted" and not value["ready"]
    assert len(env["calls"]) == 1 and value["warnings"]
    assert create(env, client_request_id=request)["candidate_id"] == value["candidate_id"]
    assert len(env["calls"]) == 1
    assert create(env)["ready"] and len(env["calls"]) == 3


def test_network_error_idempotent_without_main_state_change(env: dict[str, Any]) -> None:
    request = str(uuid.uuid4())
    row = statemachine.get_row(env["book"], 1)
    env["control"]["exception"] = llm.LLMError("离线故障")
    value = create(env, client_request_id=request)
    assert value["status"] == "error"
    assert create(env, client_request_id=request)["candidate_id"] == value["candidate_id"]
    assert len(env["calls"]) == 1 and statemachine.get_row(env["book"], 1) == row


@pytest.mark.parametrize("filename", ["source.md", "candidate.md"])
def test_tampered_snapshot_refuses_apply(env: dict[str, Any], filename: str) -> None:
    value = create(env)
    path = polish._file(env["book"], value["candidate_id"], filename)
    atomic.write_text(path, path.read_text(encoding="utf-8") + "篡改")
    with pytest.raises(polish.PolishError):
        approve(env, value)
    assert chapters.read_prose(env["book"], 1)["_body"] == BODY


@pytest.mark.parametrize("case", ["unknown_key", "bool_revision", "bad_recovery", "bad_request", "fake_ready"])
def test_manifest_strict_schema(env: dict[str, Any], case: str) -> None:
    value = create(env)
    path = polish._file(env["book"], value["candidate_id"], "manifest.json")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if case == "unknown_key":
        manifest["force"] = True
    elif case == "bool_revision":
        manifest["source_revision"] = True
    elif case == "bad_recovery":
        manifest.update(status="applying", recovery={"target": "arbitrary"})
    elif case == "bad_request":
        manifest["request"]["selected_blocks"] = "p0001"
    else:
        manifest["checks"] = {"pass": False}
    atomic.write_text(path, json.dumps(manifest, ensure_ascii=False))
    with pytest.raises(polish.PolishError) as caught:
        polish.get_candidate(env["book"], 1, value["candidate_id"])
    assert caught.value.code == "INVALID_MANIFEST"


@pytest.mark.parametrize("candidate_id", ["../outside", "..", "a/b", "C:\\outside", "not-uuid", "A" * 32])
def test_uuid_path_containment(env: dict[str, Any], candidate_id: str) -> None:
    with pytest.raises(polish.PolishError) as caught:
        polish.get_candidate(env["book"], 1, candidate_id)
    assert caught.value.code == "INVALID_CANDIDATE_ID"


def test_symlink_containment_if_supported(env: dict[str, Any]) -> None:
    root = polish._root(env["book"])
    root.mkdir(parents=True, exist_ok=True)
    candidate_id = str(uuid.uuid4())
    outside = env["home"] / "outside"
    outside.mkdir()
    try:
        (root / candidate_id).symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前 Windows 用户无符号链接权限")
    with pytest.raises(polish.PolishError) as caught:
        polish.get_candidate(env["book"], 1, candidate_id)
    assert caught.value.code == "UNSAFE_CANDIDATE_PATH"


def test_manifest_is_last_commit_and_incomplete_directory_invisible(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    writes = []
    real = atomic.write_text
    def write(path: Any, text: str, **kwargs: Any) -> None:
        path = Path(path)
        if "revisions" in path.parts:
            writes.append(path.name)
        real(path, text, **kwargs)
    monkeypatch.setattr(atomic, "write_text", write)
    value = create(env)
    assert writes[:3] == ["source.md", "candidate.md", "manifest.json"] and writes[-1] == "manifest.json"
    incomplete = polish._root(env["book"]) / str(uuid.uuid4())
    incomplete.mkdir()
    atomic.write_text(incomplete / "source.md", "不完整")
    assert [item["candidate_id"] for item in polish.list_candidates(env["book"], 1)] == [value["candidate_id"]]
    assert chapters.list_chapters(env["book"]) == [1]


def test_delete_database_candidate_remains_readable(env: dict[str, Any]) -> None:
    value = create(env)
    assert value["ready"]
    db._CONN.close()
    db._CONN = None
    db.db_path().unlink()
    db.rebuild()
    recovered = polish.get_candidate(env["book"], 1, value["candidate_id"])
    assert recovered["source"] == BODY and recovered["candidate"] == value["candidate"]
    assert recovered["status"] == "stale"
    assert polish.list_candidates(env["book"], 1)[0]["candidate_id"] == value["candidate_id"]
    assert not application_records(env)
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM generation_log").fetchone()[0] == 0


@pytest.mark.parametrize("point", ["before_write", "file_only", "after_write", "after_index", "receipt"])
def test_normal_apply_failures_rollback_only_chapter(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, point: str) -> None:
    value = create(env)
    assert value["ready"], value
    row = statemachine.get_row(env["book"], 1)
    raw = chapters.prose_path(env["book"], 1).read_bytes()
    real_write, real_finish, real_save = chapters.write_prose, polish._finish, polish._save
    if point in ("before_write", "file_only", "after_write"):
        def fail_write(book: str, no: int, fm: Any, body: str, **kwargs: Any) -> None:
            if point == "file_only":
                atomic.write_text(chapters.prose_path(book, no), FM.dump(fm, body))
            elif point == "after_write":
                real_write(book, no, fm, body, **kwargs)
            raise OSError("离线写入故障")
        monkeypatch.setattr(chapters, "write_prose", fail_write)
    elif point == "after_index":
        def fail_finish(manifest: dict[str, Any]) -> None:
            real_finish(manifest)
            raise OSError("索引已更新后故障")
        monkeypatch.setattr(polish, "_finish", fail_finish)
    else:
        def fail_save(manifest: dict[str, Any]) -> None:
            if manifest["status"] == "applied":
                raise OSError("回执落盘失败")
            real_save(manifest)
        monkeypatch.setattr(polish, "_save", fail_save)
    with pytest.raises(polish.PolishError) as caught:
        approve(env, value)
    assert caught.value.code == "APPLY_FAILED"
    assert chapters.prose_path(env["book"], 1).read_bytes() == raw
    assert statemachine.get_row(env["book"], 1) == row
    assert not application_records(env)
    assert len(env["calls"]) == 2
    assert polish.get_candidate(env["book"], 1, value["candidate_id"])["status"] == "ready"
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM generation_log").fetchone()[0] == 2


@pytest.mark.parametrize("point", ["before_write", "file_only", "after_write", "index_before_receipt", "receipt_published"])
def test_process_interrupt_recovery_is_idempotent(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, point: str) -> None:
    value = create(env)
    before = statemachine.get_row(env["book"], 1)
    real_write, real_finish, real_save = chapters.write_prose, polish._finish, polish._save
    with monkeypatch.context() as patch:
        if point in ("before_write", "file_only", "after_write"):
            def crash_write(book: str, no: int, fm: Any, body: str, **kwargs: Any) -> None:
                if point == "file_only":
                    atomic.write_text(chapters.prose_path(book, no), FM.dump(fm, body))
                elif point == "after_write":
                    real_write(book, no, fm, body, **kwargs)
                raise KeyboardInterrupt()
            patch.setattr(chapters, "write_prose", crash_write)
        elif point == "index_before_receipt":
            def crash_save(manifest: dict[str, Any]) -> None:
                if manifest["status"] == "applied":
                    raise KeyboardInterrupt()
                real_save(manifest)
            patch.setattr(polish, "_save", crash_save)
        else:
            def crash_finish(manifest: dict[str, Any]) -> None:
                real_finish(manifest)
                raise KeyboardInterrupt()
            patch.setattr(polish, "_finish", crash_finish)
        with pytest.raises(KeyboardInterrupt):
            approve(env, value)
    polish.guard_recovery(env["book"], 1)
    polish.guard_recovery(env["book"], 1)
    recovered = polish.get_candidate(env["book"], 1, value["candidate_id"])
    if point == "before_write":
        assert recovered["status"] == "ready"
        assert statemachine.get_row(env["book"], 1) == before
        assert not application_records(env)
        recovered = approve(env, value)
    assert recovered["status"] == "applied"
    assert statemachine.get_row(env["book"], 1)["state_revision"] == value["source_revision"] + 2
    assert len(application_records(env)) == 1
    assert chapters.read_prose(env["book"], 1)["_body"] == value["candidate"]
    assert approve(env, value)["receipt"] == recovered["receipt"]
    assert len(env["calls"]) == 2


@pytest.mark.parametrize("external", ["body", "revision"])
def test_recovery_never_overwrites_third_party_body_or_revision(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, external: str) -> None:
    value = create(env)
    real = chapters.write_prose
    with monkeypatch.context() as patch:
        def crash(*args: Any, **kwargs: Any) -> None:
            real(*args, **kwargs)
            raise KeyboardInterrupt()
        patch.setattr(chapters, "write_prose", crash)
        with pytest.raises(KeyboardInterrupt):
            approve(env, value)
    path = chapters.prose_path(env["book"], 1)
    if external == "body":
        atomic.write_text(path, path.read_bytes().decode("utf-8") + "外部第三种正文")
    else:
        statemachine.bump_revision(env["book"], 1)
    current = path.read_bytes()
    with pytest.raises(polish.PolishError) as caught:
        polish.guard_recovery(env["book"], 1)
    assert caught.value.code == "RECOVERY_REQUIRED"
    assert path.read_bytes() == current
    assert polish.get_candidate(env["book"], 1, value["candidate_id"])["status"] == "recovery_required"
    assert chapters.read_prose(env["book"], 1)["_body"]
    fm = {"chapter_no": 1, "version": 1, "status": "draft", "word_count": chapters.count_words(BODY)}
    with pytest.raises(polish.PolishError) as caught:
        chapters.write_prose(env["book"], 1, fm, BODY)
    assert caught.value.code == "RECOVERY_REQUIRED" and path.read_bytes() == current
    assert not application_records(env)
    from fastapi.testclient import TestClient
    from ainovel.api import app

    response = TestClient(app).post(f"/api/books/{env['book']}/chapters/1/mechcheck")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "RECOVERY_REQUIRED"
    assert path.read_bytes() == current


def test_legacy_no_request_is_preview_only_with_upgrade_warning(env: dict[str, Any]) -> None:
    value = polish.create_candidate(env["book"], 1)
    assert value["ready"] and not value["applied"] and value["warnings"]
    assert chapters.read_prose(env["book"], 1)["_body"] == BODY


@pytest.mark.parametrize("point", ["file_only", "after_write"])
def test_mechanical_api_recovers_before_changing_index(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, point: str) -> None:
    """（规格06 §9.3）重启后首个机检请求先恢复采纳，不能制造索引外改。"""
    from fastapi.testclient import TestClient
    from ainovel.api import app

    statemachine.set_status_direct(env["book"], 1, "human_review")
    env["revision"] = statemachine.get_row(env["book"], 1)["state_revision"]
    value = create(env)
    real_write = chapters.write_prose
    with monkeypatch.context() as patch:
        def crash_write(book: str, no: int, fm: Any, body: str, **kwargs: Any) -> None:
            if point == "file_only":
                atomic.write_text(chapters.prose_path(book, no), FM.dump(fm, body))
            else:
                real_write(book, no, fm, body, **kwargs)
            raise KeyboardInterrupt()
        patch.setattr(chapters, "write_prose", crash_write)
        with pytest.raises(KeyboardInterrupt):
            approve(env, value)
    response = TestClient(app).post(f"/api/books/{env['book']}/chapters/1/mechcheck")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "llm_review"
    assert polish.get_candidate(env["book"], 1, value["candidate_id"])["status"] == "applied"
    assert chapters.read_prose(env["book"], 1)["_body"] == value["candidate"]
    assert len(application_records(env)) == 1 and len(env["calls"]) == 2
