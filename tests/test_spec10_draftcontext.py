"""（规格10 §6 B）临时上下文来源、隔离、暂停及冲突回归。"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from ainovel import assembler, atomic, books, chapters, db, draftcontext, outline, pipeline, statemachine, volumeproduction
from ainovel.mockllm import MockLLM, make_prose


@pytest.fixture
def batch(book_env):
    book, home = book_env
    outline.write_locations(book, [{"名称": "青云坊", "特征": "坊市", "方位/距离": "城内", "首现章": "1", "关联剧情": "验令"},
                                  {"名称": "桥洞", "特征": "桥下", "方位/距离": "坊市南", "首现章": "1", "关联剧情": "脱身"}])
    client = MockLLM()
    for no in [1, 2, 3]:
        pipeline.generate_outline(book, no, client=client)
        pipeline.confirm_outline(book, no)
    job_dir = books.book_dir(book) / "production" / str(uuid4())
    return book, home, job_dir, client


def test_context_sources_are_only_earlier_verified_real_drafts(batch):
    book, home, job_dir, client = batch
    volumeproduction.generate_chapter(book, 1, job_dir, client=client)
    context = draftcontext.load_for_chapter(book, job_dir, 2)
    assert context["sources"] == [{"no": 1, "body_fingerprint": client_fp(book, 1),
                                    "outline_fingerprint": draftcontext.outline_fingerprint(book, 1)}]
    assert context["statuses"]["林寒"]["location"] == "青云坊"
    assert draftcontext.load_for_chapter(book, job_dir, 1)["items"] == []
    with draftcontext.scoped(book, job_dir, 2):
        assembly = assembler.assemble(book, 2)
        assert context["previous_body"].strip()[-400:] in assembly.slots["prev_ending"]
        assert "批次待审事实" in assembly.context_text()
    assert draftcontext.current(book) is None
    volumeproduction.generate_chapter(book, 2, job_dir, client=client)
    requests = list((home / "data" / "assembly_previews" / book / "requests").glob("*.json"))
    evidence = [json.loads(path.read_text(encoding="utf-8")) for path in requests]
    used = [item for item in evidence if item["meta"].get("draft_context_sources")]
    assert used
    assert all(source["no"] < item["meta"]["chapter_no"] for item in used for source in item["meta"]["draft_context_sources"])
    assert any(context["previous_body"].strip()[-400:] in item["user"] for item in used)


def client_fp(book, no):
    from ainovel import mechcheck
    return mechcheck.fingerprint(chapters.read_prose(book, no)["_body"])


@pytest.mark.parametrize("edit", ["body", "contract", "archive", "check", "schema"])
def test_source_or_dependency_edit_invalidates_suffix_without_deleting_text(batch, edit):
    book, _home, job_dir, client = batch
    volumeproduction.generate_chapter(book, 1, job_dir, client=client)
    saved = chapters.read_prose(book, 1)["_body"]
    if edit == "body":
        prose = chapters.read_prose(book, 1)
        body = prose.pop("_body")
        chapters.write_prose(book, 1, prose, body + "\n原稿修改。")
    elif edit == "contract":
        path = chapters.outline_path(book, 1)
        atomic.write_text(path, path.read_text(encoding="utf-8") + "\n作者改细纲。")
    elif edit == "archive":
        path = books.book_dir(book) / "STORY_BIBLE.md"
        atomic.write_text(path, path.read_text(encoding="utf-8") + "\n档案修改。")
    elif edit == "check":
        db.set_check_status(book_id=books.book_id(book), chapter_no=1, fingerprint=client_fp(book, 1), status="stale")
    else:
        atomic.write_text(job_dir / "draft_context.json", '{"schema_version":99,"book":"测试书","items":[]}')
    with pytest.raises((draftcontext.DraftContextError, statemachine.StateError)):
        draftcontext.load_for_chapter(book, job_dir, 2)
    assert saved in chapters.read_prose(book, 1)["_body"]
    assert not chapters.prose_path(book, 2).exists()


@pytest.mark.parametrize("invalid", ["character", "realm", "location", "new_chars", "new_locations", "new_setting", "timeline", "contradiction", "fo", "inventory"])
def test_unknown_or_contradictory_memory_pauses_without_canonical_write(batch, invalid):
    book, _home, job_dir, client = batch
    root = books.book_dir(book)
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.glob("characters/*.md")}
    def memory(no):
        result = {"chapter_no": no, "char_updates": [], "new_chars": [], "new_locations": [],
                  "foreshadow_ops": [], "event_summary": "验令", "time_elapsed": "半日", "state_delta": [
                      {"character": "林寒", "location": "青云坊", "power_level": "炼气", "physical": "旧伤", "mental": "警觉"}]}
        if invalid == "character":
            result["state_delta"][0]["character"] = "无档案者"
        elif invalid == "realm":
            result["state_delta"][0]["power_level"] = "模型自创境界"
        elif invalid == "location":
            result["state_delta"][0]["location"] = "无档案地点"
        elif invalid == "new_chars":
            result["new_chars"] = [{"name": "新角色"}]
        elif invalid == "new_locations":
            result["new_locations"] = ["新地点"]
        elif invalid == "new_setting":
            result["new_settings_pending"] = ["新规则"]
        elif invalid == "timeline":
            result["timeline_candidates"] = [{"event": "新事件"}]
        elif invalid == "contradiction":
            result["char_updates"] = [{"name": "林寒", "changes": {"location": "桥洞"}}]
        elif invalid == "fo":
            result["foreshadow_ops"] = [{"op": "plant", "fo_id": "FO-099", "note": "未知线索"}]
        else:
            result["char_updates"] = [{"name": "林寒", "changes": {"inventory": ["未定义物品"]}}]
        return result
    client.memory_factory = memory
    with pytest.raises(draftcontext.DraftContextError):
        volumeproduction.generate_chapter(book, 1, job_dir, client=client)
    assert {p.relative_to(root).as_posix(): p.read_bytes() for p in root.glob("characters/*.md")} == before
    assert not (job_dir / "draft_context.json").exists()
    assert not chapters.prose_path(book, 2).exists()


def test_scoped_stop_is_checked_before_review_and_nested_collect(batch):
    book, _home, job_dir, client = batch
    phases = []
    def stop(phase, no):
        phases.append((phase, no))
        if phase == "memory":
            raise RuntimeError("暂停")
    with draftcontext.scoped(book, check_stop=stop):
        with pytest.raises(RuntimeError, match="暂停"):
            volumeproduction.generate_chapter(book, 1, job_dir, client=client)
    assert [phase for phase, _ in phases] == ["prose", "review", "memory"]
    assert statemachine.current_status(book, 1) == "pending_finalize"
    assert not (job_dir / "draft_context.json").exists()
    before = len(client.calls)
    volumeproduction.generate_chapter(book, 1, job_dir, client=client)
    assert len(client.calls) == before + 2


@pytest.mark.parametrize("fault", ["sensitive", "not_effective", "review_fail", "overreach", "queue"])
def test_redline_and_unavailable_checks_stop_later_calls(batch, fault):
    book, home, job_dir, client = batch
    if fault == "sensitive":
        from ainovel import mechcheck
        word = mechcheck.load_sensitive_words()[0]
        client.prose_factory = lambda no, wr: word + make_prose(wr)
    elif fault == "not_effective":
        (home / "lint" / "banned_words.txt").unlink()
    elif fault == "review_fail":
        client.review_verdicts = [{"verdict": "fail", "scores": {key: 90 for key in pipeline.RUBRIC_DIMS}, "issues": []}]
    elif fault == "overreach":
        client.fix_overreach = True
        client.review_verdicts = [{"verdict": "fix", "scores": {key: 90 for key in pipeline.RUBRIC_DIMS}, "issues": [
            {"category": "prose", "severity": "blocking", "location": "章首", "quote": "雨点砸在铁皮棚上，林寒把玄铁令贴在掌心。",
             "description": "离线护栏验证", "suggestion": "只改此句"}]}]
    else:
        from ainovel import characters, conflicts
        conflicts.enqueue([characters.Conflict("new_setting", book, 0, "待人工", None, None)])
    before = len(client.calls)
    with pytest.raises(Exception):
        volumeproduction.generate_chapter(book, 1, job_dir, client=client)
    assert not chapters.prose_path(book, 2).exists()
    calls = client.calls[before:]
    if fault == "sensitive":
        assert not any("只修复问题清单" in item["user"] for item in calls)
    if fault == "queue":
        assert calls == []
    assert statemachine.current_status(book, 1) in {"outline_locked", "human_review"}


def test_context_rejects_uncontrolled_directory(batch):
    book, home, _job_dir, _client = batch
    with pytest.raises(draftcontext.DraftContextError):
        draftcontext.load_for_chapter(book, home, 2)


def test_verified_temporary_state_overrides_old_canonical_state(batch):
    book, _home, job_dir, client = batch
    def memory(no):
        return {"chapter_no": no, "char_updates": [{"name": "林寒", "changes": {"inventory": ["玄铁令"], "injuries": ["右肩旧伤"]}}],
                "new_chars": [], "new_locations": [], "foreshadow_ops": [], "event_summary": "抵达桥洞", "time_elapsed": "半日",
                "state_delta": [{"character": "林寒", "location": "桥洞", "power_level": "筑基", "mental": "警觉", "physical": "右肩旧伤"}]}
    client.memory_factory = memory
    volumeproduction.generate_chapter(book, 1, job_dir, client=client)
    with draftcontext.scoped(book, job_dir, 2):
        assembly = assembler.assemble(book, 2)
        assert "location=桥洞" in assembly.slots["char_cards"]
        assert "power_level=筑基" in assembly.slots["char_cards"]
        assert "玄铁令" in draftcontext.evidence(draftcontext.current(book, 2))
        suspect = pipeline.build_suspect_list(book, 2)
        assert "位置=桥洞" in suspect and "境界=筑基" in suspect
        mechanical = draftcontext.mech_context(book, 2, {"has_state": True, "rows": [{"character": "林寒", "power_level": "炼气"}], "prev": {}})
        assert mechanical["rows"][0]["power_level"] == "筑基"
        assert mechanical["rows"][0]["chapter_no"] == 1


def test_current_outline_mutation_stops_before_next_model_call(batch):
    book, _home, job_dir, client = batch
    def prose(no, wr):
        path = chapters.outline_path(book, no)
        atomic.write_text(path, path.read_text(encoding="utf-8") + "\n合同在生成中变化。")
        return make_prose(wr)
    client.prose_factory = prose
    before = len(client.calls)
    with pytest.raises(draftcontext.DraftContextError, match="合同"):
        volumeproduction.generate_chapter(book, 1, job_dir, client=client)
    assert len(client.calls) == before + 1
    assert chapters.prose_path(book, 1).exists()
    assert not chapters.prose_path(book, 2).exists()


def test_future_summaries_and_recall_do_not_enter_batch_request(batch):
    from ainovel import fts, summaries
    book, _home, job_dir, client = batch
    volumeproduction.generate_chapter(book, 1, job_dir, client=client)
    summaries.push(book, 9, "绝不能进入第2章的未来记忆")
    fts.index_chapter(books.book_id(book), 9, "林寒 绝不能进入第2章的未来检索")
    with draftcontext.scoped(book, job_dir, 2):
        assembly = assembler.assemble(book, 2)
        assert "绝不能进入" not in assembly.context_text()
