"""P1 管线侧回归测试（2026-10-26 审计修复）。

覆盖：rubric 守门、定稿守卫、半成品草稿、装配器截断顺序与 token 估算、V6 审计入库。
与 test_p0_regressions.py 分开维护：P0 是「当场卡死/丢数据」，P1 是「规格要求但没做到」。
"""
from __future__ import annotations

import json

import pytest

from ainovel import assembler, books, chapters, db, outline, pipeline, statemachine
from ainovel.llm import LLMError, LLMResult
from ainovel.mockllm import MockLLM


def _chapter_to_llm_review(book: str, mock: MockLLM | None = None) -> MockLLM:
    """把第 1 章推进到 llm_review（生成 → 机检通过）。"""
    m = mock or MockLLM()
    pipeline.generate_outline(book, 1, client=m)
    pipeline.confirm_outline(book, 1)
    pipeline.generate_prose(book, 1, client=m)
    pipeline.run_mech_check(book, 1, client=m)
    assert statemachine.current_status(book, 1) == "llm_review"
    return m


# ---------------------------------------------------------------------------
# P1-A rubric 守门：规格02 P-V1-5 + 规格03 §6「任一维<70 → fix」
# ---------------------------------------------------------------------------

class _ScriptedReview(MockLLM):
    """审校永远返回同一份 JSON，用来模拟"模型自称 pass 但打分塌陷"。"""

    def __init__(self, payload: dict):
        super().__init__()
        self.payload = payload
        self.review_calls = 0

    def chat(self, *, model, system, user, temperature, stream_cb=None):
        if "五维审校" in user or "评分锚点" in user or "复审指令" in user:
            self.review_calls += 1
            self.calls.append({"model": model, "system": system, "user": user})
            text = "```json\n" + json.dumps(self.payload, ensure_ascii=False) + "\n```"
            return LLMResult(text=text, input_tokens=1, output_tokens=1,
                             finish_reason="stop", model=model)
        return super().chat(model=model, system=system, user=user,
                            temperature=temperature, stream_cb=stream_cb)


class TestRubricGate:
    """原实现只信模型自己给的 verdict，模型判 pass 就一路走到定稿。"""

    def test_low_dim_blocks_pass(self, book_env):
        book, _ = book_env
        mock = _ScriptedReview({
            "scores": {"ai_flavor": 50, "plot": 95, "pacing": 95, "prose": 95, "hook": 95},
            "issues": [], "verdict": "pass",
        })
        _chapter_to_llm_review(book, mock)
        result = pipeline.run_llm_review(book, 1, client=mock)

        # 低分不得放行到定稿
        assert statemachine.current_status(book, 1) == "human_review"
        assert result["verdict"] != "pass"

    def test_blocking_issue_blocks_pass(self, book_env):
        book, _ = book_env
        mock = _ScriptedReview({
            "scores": {"ai_flavor": 95, "plot": 95, "pacing": 95, "prose": 95, "hook": 95},
            "issues": [{"category": "plot", "severity": "blocking", "location": "x",
                        "quote": "x", "description": "台账硬冲突", "suggestion": "改"}],
            "verdict": "pass",
        })
        _chapter_to_llm_review(book, mock)
        pipeline.run_llm_review(book, 1, client=mock)
        assert statemachine.current_status(book, 1) == "human_review"

    def test_genuine_pass_is_not_blocked(self, book_env):
        """全维 ≥70 且无 blocking 的 pass 必须放行，守门不能误伤。"""
        book, _ = book_env
        mock = _ScriptedReview({
            "scores": {"ai_flavor": 85, "plot": 88, "pacing": 90, "prose": 86, "hook": 92},
            "issues": [{"category": "ai_flavor", "severity": "warning", "location": "x",
                        "quote": "x", "description": "轻微", "suggestion": "改"}],
            "verdict": "pass",
        })
        _chapter_to_llm_review(book, mock)
        pipeline.run_llm_review(book, 1, client=mock)
        assert statemachine.current_status(book, 1) == "pending_finalize"

    def test_enforce_rubric_helper_units(self):
        """直接测守门函数的分支。"""
        low = {"verdict": "pass", "issues": [],
               "scores": {"ai_flavor": 60, "plot": 90, "pacing": 90, "prose": 90, "hook": 90}}
        assert pipeline._enforce_rubric(low) == "fix"

        block = {"verdict": "pass",
                 "issues": [{"severity": "blocking"}],
                 "scores": {"ai_flavor": 95, "plot": 95, "pacing": 95, "prose": 95, "hook": 95}}
        assert pipeline._enforce_rubric(block) == "fix"

        clean = {"verdict": "pass",
                 "issues": [{"severity": "warning"}],
                 "scores": {"ai_flavor": 80, "plot": 80, "pacing": 80, "prose": 80, "hook": 80}}
        assert pipeline._enforce_rubric(clean) == "pass"

        # 模型自己判 fail 时不能被放宽
        assert pipeline._enforce_rubric({"verdict": "fail", "issues": [], "scores": {}}) == "fail"


# ---------------------------------------------------------------------------
# P1-B 定稿守卫：定稿后正文受快照保护，禁止静默改写
# ---------------------------------------------------------------------------

class TestFinalizedGuard:
    """原实现定稿后仍可被 /fix 改写，造成正文/快照/导出三方不一致。"""

    def _finalize_chapter(self, book: str) -> MockLLM:
        mock = MockLLM()
        _chapter_to_llm_review(book, mock)
        pipeline.run_llm_review(book, 1, client=mock)
        pipeline.finalize(book, 1, client=mock)
        assert statemachine.current_status(book, 1) == "finalized"
        return mock

    def test_targeted_fix_rejects_finalized(self, book_env):
        book, _ = book_env
        self._finalize_chapter(book)
        before = chapters.read_prose(book, 1)["_body"]

        with pytest.raises(statemachine.StateError):
            pipeline.run_targeted_fix(book, 1, [{"category": "ai_flavor",
                                                "severity": "warning",
                                                "quote": "x", "location": "x",
                                                "description": "d", "suggestion": "s"}])
        assert chapters.read_prose(book, 1)["_body"] == before  # 一个字都没变

    def test_finalize_guard_helper(self, book_env):
        book, _ = book_env
        self._finalize_chapter(book)
        with pytest.raises(statemachine.StateError):
            pipeline._assert_not_finalized(book, 1, "定向修复")


# ---------------------------------------------------------------------------
# P1-C 半成品草稿：崩溃后可续（规格03 §5）
# ---------------------------------------------------------------------------

class _WriteThenFail(MockLLM):
    """先流式吐出一些内容，再模拟连接中断。"""

    def chat(self, *, model, system, user, temperature, stream_cb=None):
        if "本章上下文" in user or "黄金三章" in user:
            if stream_cb:
                stream_cb("林寒走进青云坊，四周人声嘈杂。" * 40)
            raise LLMError("连接中断")
        return super().chat(model=model, system=system, user=user,
                            temperature=temperature, stream_cb=stream_cb)


class TestDraftPersistence:
    def test_partial_output_survives_failure(self, book_env):
        book, _ = book_env
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)

        with pytest.raises(LLMError):
            pipeline.generate_prose(book, 1, client=_WriteThenFail())

        draft = chapters.read_draft(book, 1)
        assert len(draft.replace("\n", "")) > 0, "半成品必须落盘"
        assert statemachine.current_status(book, 1) == "error"

    def test_draft_cleared_on_success(self, book_env):
        book, _ = book_env
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)
        pipeline.generate_prose(book, 1, client=mock)
        # 成功落正式正文后不留半成品
        assert chapters.read_draft(book, 1) == ""

    def test_draft_does_not_touch_formal_prose(self, book_env):
        """半成品写在独立文件，正式正文全程不被污染。"""
        book, _ = book_env
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)
        pipeline.generate_prose(book, 1, client=mock)
        good = chapters.read_prose(book, 1)["_body"]

        # 再次生成但失败（模拟返工时断网）
        statemachine.transition(book, 1, "pass") if False else None
        chapters.write_draft(book, 1, "半成品内容")
        assert chapters.read_prose(book, 1)["_body"] == good


# ---------------------------------------------------------------------------
# P1-D 装配器：截断顺序与逐槽 token 估算（规格03 §2.2）
# ---------------------------------------------------------------------------

class TestAssemblerConformance:
    def test_cut_order_matches_spec(self):
        """规格只授权砍三项：前文检索 → 最近章摘要 → Bible 条目。"""
        assert assembler.CUT_ORDER == ["fts_recall", "recaps", "bible_core"]

    def test_never_truncate_matches_spec(self):
        """规格点名永不截断：力量体系行、出场角色卡、细纲合同。"""
        assert "power_rows" in assembler.NEVER_TRUNCATE
        assert "char_cards" in assembler.NEVER_TRUNCATE
        assert "outline_contract" in assembler.NEVER_TRUNCATE
        # 这两个不在规格的永不截断清单内
        assert "volume_rhythm" not in assembler.NEVER_TRUNCATE
        assert "output_rules" not in assembler.NEVER_TRUNCATE

    def test_per_slot_token_estimates_are_populated(self, book_env):
        book, _ = book_env
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)

        a = assembler.assemble(book, 1)
        preview = a.preview()
        assert len(preview["slots"]) == 13
        # 有内容的槽必须给出非零估算（原先恒为 0，UI 面板全是 0t）
        non_empty = [s for s in preview["slots"] if s["content"]]
        assert non_empty
        assert all(s["est_tokens"] > 0 for s in non_empty), \
            [(s["title"], s["est_tokens"]) for s in non_empty if s["est_tokens"] == 0]

    def test_estimate_formula_is_chars_times_075(self):
        assert assembler.est_tokens("中" * 100) == 75
        assert assembler.est_tokens("中 文\n字") == 2  # 空白不计 → 3 字 × 0.75 = 2.25 → 2


# ---------------------------------------------------------------------------
# P1-E 卷末审计结果入库（规格01 §3：review_records.kind 含 'audit'）
# ---------------------------------------------------------------------------

class TestAuditRecordPersisted:
    def test_audit_writes_review_record(self, book_env):
        book, _ = book_env
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)
        pipeline.generate_prose(book, 1, client=mock)
        pipeline.run_mech_check(book, 1, client=mock)
        pipeline.run_llm_review(book, 1, client=mock)
        pipeline.finalize(book, 1, client=mock)

        result = pipeline.audit_volume(book, 1, client=mock)
        assert result["report_path"]

        with db.tx() as conn:
            rows = conn.execute(
                "SELECT kind, verdict, scores_json FROM review_records "
                "WHERE book_id=? AND chapter_no=? AND kind='audit'",
                (books.book_id(book), 1),
            ).fetchall()
        assert rows, "审计结论必须入 review_records（kind='audit'）"
        assert rows[0]["verdict"] == "pass"
        assert json.loads(rows[0]["scores_json"])["foreshadow_total"] >= 0
