"""（规格10 §6 A/B）候选发动机隔离回归，模型全部离线。"""
from __future__ import annotations

import copy
import json
from uuid import uuid4

import pytest

from ainovel import atomic, books, chapters, draftcontext, frontmatter as FM, outline, pipeline, planning, statemachine, volumeproduction
from ainovel.mockllm import MockLLM


def request(numbers: list[int], volume: int = 1, mode: str = "prepare") -> dict:
    return {"mode": mode, "volume_no": volume, "count": len(numbers), "numbers": numbers,
            "logline": "林寒为了寻找父母，必须查清玄铁令，但坊市护卫正在追踪他", "requirements": "推进线索"}


def publish(book: str, bundle: dict, numbers: list[int]) -> None:
    """测试模拟一个真实用户动作；生产 worker 不得调用此函数。"""
    for path, text in volumeproduction.validate_bundle(book, bundle, numbers).items():
        atomic.write_text(books.book_dir(book) / path, text)
    for no in numbers:
        statemachine.ensure_chapter_row(book, no)
        statemachine.transition(book, no, "generate_outline")
        pipeline.confirm_outline(book, no)


def test_prepare_empty_book_never_adopts(home):
    book = "空书"
    books.create_book(book)
    root = books.book_dir(book)
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.glob("**/*.md")}
    client = MockLLM()
    progress = []
    bundle = volumeproduction.prepare(book, request([1, 2, 3]), client=client,
                                      progress=lambda phase, no: progress.append((phase, no)))
    assert set(bundle) == {"schema_version", "outline", "files", "outlines", "notes"}
    assert [item["no"] for item in bundle["outlines"]] == [1, 2, 3]
    assert all(item["fm"]["confirmed"] is False for item in bundle["outlines"])
    assert bundle["outline"]["fm"]["planning"]["adoptions"] == {}
    assert {p.relative_to(root).as_posix(): p.read_bytes() for p in root.glob("**/*.md")} == before
    assert statemachine.list_chapters(book) == []
    assert len(progress) == len(client.calls)
    files = volumeproduction.validate_bundle(book, bundle, [1, 2, 3])
    assert "OUTLINE.md" in files and "chapters/003_细纲.md" in files
    assert FM.parse(files["chapters/003_细纲.md"])[0]["confirmed"] is False
    assert FM.parse(files["OUTLINE.md"])[0]["planning"]["adoptions"]["global"]["source"] == "human"
    second = client.calls[-2]["user"]
    assert "尚无已写原文" in second and "前章计划合同而非已写原文" in second
    assert "〖" not in "\n".join(call["user"] for call in client.calls)


def test_prepare_reuses_archives_and_preserves_non_target_rows(book_env):
    book, _ = book_env
    old = outline.read_outline(book)
    client = MockLLM()
    bundle = volumeproduction.prepare(book, request([4, 5, 6]), client=client)
    assert bundle["files"] == {}
    assert [r for r in bundle["outline"]["rows"] if int(r["章"]) not in [4, 5, 6]] == [
        r for r in old["chapter_rows"] if int(r["章"]) not in [4, 5, 6]]
    assert not any("世界观架构师" in call["user"] or "角色设计师" in call["user"] for call in client.calls)


@pytest.mark.parametrize("change", ["path", "confirmed", "range", "row", "volume", "unit", "adoptions", "bible", "character", "fo", "header"])
def test_validation_rejects_corrupt_or_overreaching_candidate(book_env, change):
    book, _ = book_env
    bundle = volumeproduction.prepare(book, request([1, 2, 3]), client=MockLLM())
    bad = copy.deepcopy(bundle)
    if change == "path":
        bad["files"]["../config.json"] = "{}"
    elif change == "confirmed":
        bad["outlines"][0]["fm"]["confirmed"] = True
    elif change == "range":
        bad["outlines"][0]["no"] = 7
    elif change == "row":
        bad["outline"]["rows"][-1]["本章目标"] = "篡改旧章"
    elif change == "volume":
        bad["outline"]["fm"]["volumes"][0]["chapters"] = [1, 14]
    elif change == "unit":
        bad["outline"]["fm"]["planning"]["units"][0]["chapters"] = [1, 4]
    elif change == "adoptions":
        bad["outline"]["fm"]["planning"]["adoptions"]["global"] = {"source": "ai"}
    elif change == "bible":
        bad["files"]["STORY_BIBLE.md"] = "## 基础设定\n\n覆盖世界\n"
    elif change == "character":
        bad["outlines"][0]["fm"]["pov"] = "无档案者"
    elif change == "fo":
        bad["outlines"][0]["fm"]["foreshadow_ops"] = [{"op": "pay", "fo_id": "FO-001", "note": "回收"}]
    else:
        del bad["outline"]["rows"][0]["爽点/揭露"]
    with pytest.raises((volumeproduction.VolumeProductionError, draftcontext.DraftContextError, FM.FrontmatterError)):
        volumeproduction.validate_bundle(book, bad, [1, 2, 3])


def test_revalidation_protects_newly_locked_outline(book_env):
    book, _ = book_env
    bundle = volumeproduction.prepare(book, request([1, 2, 3]), client=MockLLM())
    pipeline.generate_outline(book, 2, client=MockLLM())
    pipeline.confirm_outline(book, 2)
    with pytest.raises(volumeproduction.VolumeProductionError, match="禁止覆盖"):
        volumeproduction.validate_bundle(book, bundle, [1, 2, 3])


def test_stop_callback_runs_before_every_paid_request(home):
    book = "暂停书"
    books.create_book(book)
    client = MockLLM()
    def stop(phase, no):
        raise RuntimeError("用户暂停")
    with pytest.raises(RuntimeError, match="用户暂停"):
        volumeproduction.prepare(book, request([1]), client=client, progress=stop)
    assert client.calls == []


def test_three_chapters_run_to_pending_finalize_without_archive_pollution(book_env, monkeypatch):
    book, _ = book_env
    outline.write_locations(book, [{"名称": "青云坊", "特征": "坊市", "方位/距离": "城内", "首现章": "1", "关联剧情": "验令"}])
    client = MockLLM()
    bundle = volumeproduction.prepare(book, request([1, 2, 3]), client=client)
    publish(book, bundle, [1, 2, 3])
    root = books.book_dir(book)
    official = [root / "STORY_BIBLE.md", root / "PLOT_POINTS.md", root / "LOCATIONS.md"] + list((root / "characters").glob("*.md"))
    before = {str(path): path.read_bytes() for path in official}
    monkeypatch.setattr(pipeline, "finalize", lambda *args, **kwargs: pytest.fail("worker 自动定稿"))
    job_dir = root / "production" / str(uuid4())
    for no in [1, 2, 3]:
        result = volumeproduction.generate_chapter(book, no, job_dir, client=client)
        assert result["status"] == statemachine.current_status(book, no) == "pending_finalize"
        assert result["check"]["fingerprint"] == result["fingerprint"]
        assert result["review"]["verdict"] == "pass"
        assert not chapters.memory_path(book, no).exists()
    assert {str(path): path.read_bytes() for path in official} == before
    assert not (root / "STATE.md").exists()
    context = draftcontext.load_for_chapter(book, job_dir, 3)
    assert [item["no"] for item in context["items"]] == [1, 2]
    assert context["previous_body"] == chapters.read_prose(book, 2)["_body"]
    assert json.loads((job_dir / "draft_context.json").read_text(encoding="utf-8"))["items"][2]["no"] == 3
    before_calls = len([call for call in client.calls if "本章上下文" in call["user"] or "黄金三章" in call["user"]])
    volumeproduction.generate_chapter(book, 3, job_dir, client=client)
    assert len([call for call in client.calls if "本章上下文" in call["user"] or "黄金三章" in call["user"]]) == before_calls


def test_fo_operation_sequence_is_candidate_only(book_env):
    book, _ = book_env
    bundle = volumeproduction.prepare(book, request([1, 2, 3]), client=MockLLM())
    for item, op in zip(bundle["outlines"], ["plant", "advance", "pay"]):
        item["fm"]["foreshadow_ops"] = [{"op": op, "fo_id": "FO-002", "note": "令牌新线索"}]
    before = (books.book_dir(book) / "PLOT_POINTS.md").read_bytes()
    files = volumeproduction.validate_bundle(book, bundle, [1, 2, 3])
    assert "PLOT_POINTS.md" not in files
    assert (books.book_dir(book) / "PLOT_POINTS.md").read_bytes() == before
    bundle["outlines"][1]["fm"]["foreshadow_ops"][0]["op"] = "pay"
    with pytest.raises(draftcontext.DraftContextError):
        volumeproduction.validate_bundle(book, bundle, [1, 2, 3])


def test_append_keeps_locked_old_chapter_and_adoption_records(book_env):
    book, _ = book_env
    initial = volumeproduction.prepare(book, request([1, 2, 3]), client=MockLLM())
    publish(book, initial, [1, 2, 3])
    before = chapters.outline_path(book, 1).read_bytes()
    old = outline.read_outline(book)
    bundle = volumeproduction.prepare(book, request([13, 14, 15], mode="append"), client=MockLLM())
    assert chapters.outline_path(book, 1).read_bytes() == before
    assert bundle["outline"]["fm"]["volumes"][0]["chapters"] == [1, 15]
    assert bundle["outline"]["fm"]["planning"]["adoptions"] == old["planning"]["adoptions"]
    projected = FM.parse(volumeproduction.validate_bundle(book, bundle, [13, 14, 15])["OUTLINE.md"])[0]
    for key, value in old["planning"]["adoptions"].items():
        if key.startswith("chapters:"):
            assert projected["planning"]["adoptions"][key] == value


def test_new_volume_and_small_g5_batches_use_server_numbers(book_env):
    book, _ = book_env
    client = MockLLM()
    numbers = list(range(13, 25))
    bundle = volumeproduction.prepare(book, request(numbers, volume=2, mode="new_volume"), client=client)
    assert bundle["outline"]["fm"]["volumes"][0]["chapters"] == [1, 12]
    assert bundle["outline"]["fm"]["volumes"][1]["chapters"] == [13, 24]
    assert all(item["fm"]["volume"] == 2 for item in bundle["outlines"])
    chapters_calls = [call for call in client.calls if "- 规划作用域：chapters" in call["user"]]
    assert len(chapters_calls) == 2
    assert all("未人工采纳" in call["user"] for call in chapters_calls)
    files = volumeproduction.validate_bundle(book, bundle, numbers)
    assert files == volumeproduction.validate_bundle(book, copy.deepcopy(bundle), numbers)


def test_candidate_render_preserves_author_notes(book_env):
    book, _ = book_env
    path = books.book_dir(book) / "OUTLINE.md"
    note = "## 作者备注\n\n这一段保留原样，不属于本批规划。\n"
    atomic.write_text(path, path.read_text(encoding="utf-8") + "\n" + note)
    bundle = volumeproduction.prepare(book, request([1, 2, 3]), client=MockLLM())
    rendered = volumeproduction.validate_bundle(book, bundle, [1, 2, 3])["OUTLINE.md"]
    assert note in rendered


@pytest.mark.parametrize("prompt", ["G5", "G6"])
def test_incompatible_book_prompt_fails_before_preparation_calls(home, prompt):
    from ainovel import prompts
    book = "旧模板书"
    books.create_book(book)
    p = prompts.get(prompt, book)
    body = p.body.replace("〖批次计划说明〗", "旧版无此输入") if prompt == "G6" else p.body.replace("卷联合候选", "旧规划")
    atomic.write_text(books.book_dir(book) / "prompts" / "generate" / f"{prompt}.md", FM.dump(p.fm, body))
    client = MockLLM()
    with pytest.raises(volumeproduction.VolumeProductionError, match="PROMPT_UPGRADE_REQUIRED"):
        volumeproduction.prepare(book, request([1]), client=client)
    assert client.calls == []


def test_empty_book_can_run_three_real_mock_drafts(home):
    book = "新卷出稿"
    books.create_book(book)
    client = MockLLM()
    bundle = volumeproduction.prepare(book, request([1, 2, 3]), client=client)
    publish(book, bundle, [1, 2, 3])
    job_dir = books.book_dir(book) / "production" / str(uuid4())
    for no in [1, 2, 3]:
        result = volumeproduction.generate_chapter(book, no, job_dir, client=client)
        assert result["status"] == "pending_finalize"
        assert result["review"]["scores"]["plot"] == 90
    assert len(json.loads((job_dir / "draft_context.json").read_text(encoding="utf-8"))["items"]) == 3
