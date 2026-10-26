"""P0 缺陷回归测试（2026-10-26 审计修复）。

每个用例对应一处曾会让用户当场卡死或静默丢数据的缺陷，防复发。
全部走 conftest.py 的 home fixture（临时目录）+ MockLLM，不碰真实数据、不联网。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ainovel import books, chapters, characters, frontmatter as FM, outline, pipeline, \
    settings, statemachine
from ainovel.api import app
from ainovel.llm import LLMError, LLMResult
from ainovel.mockllm import MockLLM

client = TestClient(app)


# ---------------------------------------------------------------------------
# P0-1 正文生成中途异常不得让章节永久卡死在 generating（规格03 §3）
# ---------------------------------------------------------------------------

class TestGeneratingRecoverable:
    """原实现 generating 只有 complete 一条出边，任何 LLM 异常都会让该章报废。"""

    class _Flaky:
        """模拟网络抖动：chat 必失败。"""

        def chat(self, **_kw):
            raise LLMError("HTTP 503: 服务暂时不可用")

    def test_failure_lands_in_error_not_generating(self, book_env):
        book, _ = book_env
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)
        assert statemachine.current_status(book, 1) == "outline_locked"

        with pytest.raises(LLMError):
            pipeline.generate_prose(book, 1, client=self._Flaky())

        # 关键断言：必须落到可救援的 error，而不是卡死的 generating
        assert statemachine.current_status(book, 1) == "error"

    def test_retry_after_failure_succeeds(self, book_env):
        book, _ = book_env
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)
        with pytest.raises(LLMError):
            pipeline.generate_prose(book, 1, client=self._Flaky())

        # 用户点「重试」（= /retry 路由的 transition('retry')）后应能正常生成
        statemachine.transition(book, 1, "retry")
        r = pipeline.generate_prose(book, 1, client=mock)
        assert r["word_count"] > 0
        assert statemachine.current_status(book, 1) == "mech_check"

    def test_error_state_has_retry_edge(self):
        """状态机本身必须有 generating --fail--> error 这条出边。"""
        assert statemachine.TRANSITIONS[("generating", "fail")] == "error"
        assert statemachine.TRANSITIONS[("generating", "abort")] == "outline_locked"

    def test_retry_lands_on_clean_state_not_generating(self, book_env):
        """retry 必须回到可继续的干净状态，不能回到 generating 这个瞬态。

        generating 是「生成进行中」的瞬态，若 prev_status 记成它，retry 后
        正文可能是空的、用户也看不出下一步该做什么（半残态）。
        """
        book, _ = book_env
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)
        with pytest.raises(LLMError):
            pipeline.generate_prose(book, 1, client=self._Flaky())

        restored = statemachine.transition(book, 1, "retry")
        assert restored == "outline_locked"
        # 确认这个状态下可以直接重新生成
        r = pipeline.generate_prose(book, 1, client=mock)
        assert r["word_count"] > 0


# ---------------------------------------------------------------------------
# P0-2 设置的掩码占位串不得覆盖真实 api_key
# ---------------------------------------------------------------------------

class TestMaskedKeyNotPersisted:
    """原实现：UI 把 masked() 的占位串 PUT 回来 → 真实 key 永久丢失且 has_key 仍为 true。"""

    def test_masked_roundtrip_preserves_real_key(self, home):
        settings.update({"providers": {"openai": {"api_key": "sk-REAL-SECRET"}}})
        masked = settings.masked()
        placeholder = masked["providers"]["openai"]["api_key"]
        assert placeholder != "sk-REAL-SECRET"

        # 模拟前端「保存连接」：把掩码串原样提交
        settings.update({"providers": {"openai": {"api_key": placeholder}}})
        assert settings.load()["providers"]["openai"]["api_key"] == "sk-REAL-SECRET"

    def test_masked_save_still_updates_other_fields(self, home):
        """拦掩码串不能顺带拦掉同一请求里的 base_url 等合法变更。"""
        settings.update({"providers": {"openai": {"api_key": "sk-REAL", "base_url": "https://a/v1"}}})
        placeholder = settings.masked()["providers"]["openai"]["api_key"]
        settings.update({"providers": {"openai": {"api_key": placeholder, "base_url": "https://b/v1"}}})
        prov = settings.load()["providers"]["openai"]
        assert prov["api_key"] == "sk-REAL"
        assert prov["base_url"] == "https://b/v1"

    def test_new_real_key_still_takes_effect(self, home):
        """用户真的换了新 key 时不能被误拦。"""
        settings.update({"providers": {"openai": {"api_key": "sk-OLD"}}})
        settings.update({"providers": {"openai": {"api_key": "sk-NEW"}}})
        assert settings.load()["providers"]["openai"]["api_key"] == "sk-NEW"


# ---------------------------------------------------------------------------
# P0-4 human_rewrite 不得清空正文 / 不得在非法状态下毁数据
# ---------------------------------------------------------------------------

def _to_human_review(book: str, mock: MockLLM) -> None:
    """把第 1 章推到 human_review（该状态没有快照，正是原缺陷最易触发的情形）。"""
    pipeline.generate_outline(book, 1, client=mock)
    pipeline.confirm_outline(book, 1)
    pipeline.generate_prose(book, 1, client=mock)
    pipeline.run_mech_check(book, 1, client=mock)
    mock.review_verdicts = [{
        "scores": {"ai_flavor": 50, "plot": 50, "pacing": 50, "prose": 50, "hook": 50},
        "issues": [{"category": "ai_flavor", "severity": "blocking", "location": "全章",
                    "quote": "x", "description": "d", "suggestion": "s"}],
        "verdict": "fail",
    }]
    pipeline.run_llm_review(book, 1, client=mock)
    assert statemachine.current_status(book, 1) == "human_review"


class TestRewriteKeepsProse:
    """原实现 base_version=None 时用空 body 覆盖同一路径，2541 字正文变 0。"""

    def test_rewrite_without_snapshot_keeps_body(self, book_env):
        book, _ = book_env
        mock = MockLLM()
        _to_human_review(book, mock)
        before = chapters.read_prose(book, 1)["_body"]
        assert before.strip()

        new_version = pipeline.human_rewrite(book, 1, None)

        after = chapters.read_prose(book, 1)["_body"]
        assert after == before  # 正文原样带入新版本，不被清空
        assert new_version == 2
        assert statemachine.current_status(book, 1) == "generating"

    def test_rewrite_from_snapshot_uses_snapshot(self, book_env):
        book, _ = book_env
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)
        pipeline.generate_prose(book, 1, client=mock)
        pipeline.run_mech_check(book, 1, client=mock)
        pipeline.run_llm_review(book, 1, client=mock)
        pipeline.finalize(book, 1, client=mock)
        snap = pipeline.get_snapshot(book, 1, 1)
        assert snap

        pipeline.human_rewrite(book, 1, 1)
        assert chapters.read_prose(book, 1)["_body"] == snap

    def test_rewrite_rejects_illegal_state_without_touching_data(self, book_env):
        """非法状态必须在落盘前拒绝，不能「先毁数据再抛错」。"""
        book, _ = book_env
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)  # → outline_locked，不可重写

        with pytest.raises(statemachine.StateError):
            pipeline.human_rewrite(book, 1, None)
        assert statemachine.current_status(book, 1) == "outline_locked"

    def test_rewrite_rejects_missing_snapshot(self, book_env):
        book, _ = book_env
        mock = MockLLM()
        _to_human_review(book, mock)
        before = chapters.read_prose(book, 1)["_body"]
        with pytest.raises(pipeline.PipelineError):
            pipeline.human_rewrite(book, 1, 99)
        assert chapters.read_prose(book, 1)["_body"] == before  # 未被改动


# ---------------------------------------------------------------------------
# P0-5 档案保存不得污染 frontmatter / 不得丢节奏总谱
# ---------------------------------------------------------------------------

class TestArchiveWriteDoesNotPollute:
    """原实现：前端整包回传 GET 结果，_body/_file 被当 frontmatter 写盘，
    _rhythm 缺失导致「爽点节奏总谱」每次保存被重置为「（待规划）」。"""

    def _make_book_with_rhythm(self) -> str:
        name = "污染测试书"
        books.create_book(name, genre_template="urban")
        outline.write_outline(
            name,
            {"logline": "L", "ending_direction": "E",
             "volumes": [{"no": "1", "name": "卷名", "chapters": [1, 12],
                          "core_conflict": "CC", "goal": "G", "climax": "CL", "hook_out": "HO"}]},
            rows=[{"章": "1", "卷": "1", "本章目标": "目标1", "冲突来源": "冲突1",
                   "爽点/揭露": "爽点1", "钩子类型": "悬念", "重要度★": "★3"}],
            rhythm="这是爽点节奏总谱的正文，非常重要",
        )
        return name

    def test_outline_save_preserves_rhythm_and_strips_body(self, home):
        name = self._make_book_with_rhythm()
        got = client.get(f"/api/books/{name}/outline").json()
        got["chapter_rows"].append({"章": "2", "卷": "1", "本章目标": "目标2",
                                   "冲突来源": "冲突2", "爽点/揭露": "爽点2",
                                   "钩子类型": "选择", "重要度★": "★4"})
        assert client.put(f"/api/books/{name}/outline", json=got).status_code == 200

        fm, body = FM.read_file(books.book_dir(name) / "OUTLINE.md")
        assert "_body" not in fm                     # 不再污染 frontmatter
        assert "这是爽点节奏总谱的正文" in body          # 节奏总谱没丢
        assert len(outline.read_outline(name)["chapter_rows"]) == 2

    def test_repeated_outline_saves_are_stable(self, home):
        name = self._make_book_with_rhythm()
        for _ in range(3):
            got = client.get(f"/api/books/{name}/outline").json()
            client.put(f"/api/books/{name}/outline", json=got)
        fm, body = FM.read_file(books.book_dir(name) / "OUTLINE.md")
        assert "_body" not in fm
        assert "这是爽点节奏总谱的正文" in body

    def test_outline_put_strips_body_even_if_client_sends_it(self, home):
        """即使前端不清理，后端也必须兜住（防御层）。"""
        name = self._make_book_with_rhythm()
        client.put(f"/api/books/{name}/outline",
                   json={"logline": "L2", "chapter_rows": [], "_body": "脏数据"})
        fm, _ = FM.read_file(books.book_dir(name) / "OUTLINE.md")
        assert "_body" not in fm
        assert fm["logline"] == "L2"

    def test_character_save_strips_private_fields(self, home):
        name = "角色污染书"
        books.create_book(name, genre_template="urban")
        characters.create(name, "林寒", role="主角")
        got = client.get(f"/api/books/{name}/characters/林寒").json()
        assert "_file" in got  # 读接口确实会带内部字段

        assert client.put(f"/api/books/{name}/characters/林寒",
                          json=dict(got)).status_code == 200
        fm, _ = FM.read_file(characters.file_for(name, "林寒"))
        assert "_file" not in fm
        assert "_body" not in fm
        assert fm["name"] == "林寒"


# ---------------------------------------------------------------------------
# P0-3 G5 变量契约：前端必须传全 5 个变量，后端严格渲染不得放水
# ---------------------------------------------------------------------------

class TestG5VariableContract:
    """原实现前端只传 3 个变量 → 严格渲染抛错 → 用户点 G5 必得 404。"""

    def test_g5_renders_with_all_required_vars(self, home):
        from ainovel import prompts

        _, text = prompts.render("G5", {
            "世界观摘要": "都市异能", "角色卡摘要": "林寒（主角）",
            "logline": "主角为了X必须做Y但障碍是Z",
            "目标总章数": "120", "单章字数区间": "2200-2800",
        })
        assert "〖" not in text  # 渲染后不得残留变量标记

    def test_g5_rejects_missing_vars(self, home):
        """守门本身是对的：缺变量必须报错，不能静默发半成品给模型。"""
        from ainovel import prompts

        with pytest.raises(prompts.PromptError) as ei:
            prompts.render("G5", {"logline": "x", "目标总章数": "120"})
        msg = str(ei.value)
        assert "世界观摘要" in msg and "角色卡摘要" in msg
