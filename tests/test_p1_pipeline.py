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


class TestTermCrosscheck:
    """术语预检必须双向。

    旧实现只查「术语表里的词没在正文出现」——33 条术语逐条报缺席，全是噪声，
    夹在 6 条状态 diff 里没人看。而真问题（正文冒出来的新名词在圣经里查不到定义）
    完全没人查，于是审校把 NH-01/NH-02 当成 FO-013「LT77-SQ82」的冲突报出来，
    白烧一轮修复额度。
    """

    def _define(self, book: str, term_line: str) -> None:
        from ainovel import bible

        sec = bible.read(book)
        sec["术语表"] = sec["术语表"].rstrip() + "\n" + term_line
        bible.write(book, sec)

    def test_undefined_code_reported_defined_one_silent(self, book_env):
        book, _ = book_env
        self._define(book, "| NH-01 | 休眠舱活体编号 |")
        prose = "他摸到 NH-01 的卡槽，又看见钢印上刻着 XY-99。"
        joined = "\n".join(pipeline._term_crosscheck(book, prose, {}))
        assert "XY-99" in joined, "未定义编号必须报出来"
        assert "NH-01" not in joined, "术语表已定义，不该再报"

    def test_cjk_candidate_strips_stop_prefix(self, book_env):
        """中文没有词边界，正向取字会把「没有太昊门阀」整串当成名字。"""
        text = "没有太昊门阀的许可。太昊门阀的人却已经到了。"
        counts = pipeline._proper_noun_counts(text)
        assert "太昊门阀" in counts, f"专名没认出来：{counts}"
        assert not any(c.startswith("没有") or c.startswith("那是") for c in counts), counts

    def test_term_named_in_outline_but_absent_in_prose(self, book_env):
        book, _ = book_env
        contract = {"purpose": "拿到玄铁令并脱身", "scenes": [], "hook": {"content": ""}}
        lines = pipeline._term_crosscheck(book, "他什么也没拿到。", contract)
        assert any("玄铁令" in l and "缺席" in l for l in lines), lines

    def test_single_weak_signal_not_reported(self, book_env):
        """只出现一次的中文后缀词是行文巧合，不算设定，不许刷进清单。"""
        book, _ = book_env
        lines = pipeline._term_crosscheck(book, "他看了一眼那口井。", {})
        assert not any("井" in l for l in lines), lines


class TestOutlineBannedGuard:
    """禁词源头在细纲，就该在细纲拦。

    第 12 章实测：「云雾缭绕」先写进细纲 hook，W1 原样继承进正文，机检 M-02
    判 block，再走 V5 定向修复——同一处问题白烧两次调用，还占掉一次修复轮次。
    """

    def test_banned_word_in_hook_rejected(self, book_env):
        book, _ = book_env
        fm = {"hook": {"type": "悬念", "content": "山门之外云雾缭绕，他把手按在刀上。"}}
        with pytest.raises(pipeline.PipelineError, match="禁词"):
            pipeline._validate_outline_banned(fm, book)

    def test_banned_word_in_scene_goal_rejected(self, book_env):
        book, _ = book_env
        fm = {"scenes": [{"goal": "穿过云雾缭绕的回廊", "conflict": "守卫盘查"}]}
        with pytest.raises(pipeline.PipelineError, match="禁词"):
            pipeline._validate_outline_banned(fm, book)

    def test_clean_outline_passes(self, book_env):
        book, _ = book_env
        pipeline._validate_outline_banned(
            {"hook": {"content": "他推开门，直接看见了她手里那半截断箭。"}}, book)

    def test_book_whitelist_respected(self, book_env):
        book, _ = book_env
        cfg = books.load_config(book)
        cfg["lint_whitelist"] = ["云雾缭绕"]
        books.save_config(book, cfg)
        pipeline._validate_outline_banned({"hook": {"content": "云雾缭绕"}}, book)


class TestForeshadowDrift:
    """细纲声明的伏笔操作 vs 记忆卡实际执行的操作。

    第 11 章实测：细纲写 pay FO-001，正文没真正回收，V4 抽出 advance，台账停在
    building。设计上以记忆卡为准是对的（正文写了什么才算数），但没人提示，
    第 12 章就又安排了一次回收——同一个伏笔被回收两遍。
    """

    def _patch(self, monkeypatch, declared, actual):
        monkeypatch.setattr(chapters, "read_outline_contract",
                            lambda b, n: {"foreshadow_ops": declared})
        monkeypatch.setattr(chapters, "read_memory", lambda b, n: {"foreshadow_ops": actual})

    def test_op_mismatch_reported(self, book_env, monkeypatch):
        book, _ = book_env
        self._patch(monkeypatch,
                    [{"fo_id": "FO-001", "op": "pay"}],
                    [{"fo_id": "FO-001", "op": "advance"}])
        d = pipeline.foreshadow_drift(book, 1)
        assert len(d) == 1, d
        assert "FO-001" in d[0] and "pay" in d[0] and "advance" in d[0]

    def test_missing_in_card_reported(self, book_env, monkeypatch):
        book, _ = book_env
        self._patch(monkeypatch, [{"fo_id": "FO-009", "op": "advance"}], [])
        d = pipeline.foreshadow_drift(book, 1)
        assert len(d) == 1 and "未出现" in d[0]

    def test_consistent_ops_silent(self, book_env, monkeypatch):
        book, _ = book_env
        self._patch(monkeypatch,
                    [{"fo_id": "FO-002", "op": "plant"}],
                    [{"fo_id": "FO-002", "op": "plant"}, {"fo_id": "FO-003", "op": "advance"}])
        assert pipeline.foreshadow_drift(book, 1) == []

    def test_no_declared_ops_silent(self, book_env, monkeypatch):
        book, _ = book_env
        self._patch(monkeypatch, [], [{"fo_id": "FO-001", "op": "pay"}])
        assert pipeline.foreshadow_drift(book, 1) == []

    def test_missing_card_does_not_crash(self, book_env, monkeypatch):
        """记忆卡还没生成时（定稿前的章节）不能抛错，否则定稿返回体直接崩。"""
        book, _ = book_env
        monkeypatch.setattr(chapters, "read_outline_contract",
                            lambda b, n: {"foreshadow_ops": [{"fo_id": "FO-001", "op": "pay"}]})

        def _boom(b, n):
            raise FileNotFoundError("no card")

        monkeypatch.setattr(chapters, "read_memory", _boom)
        assert pipeline.foreshadow_drift(book, 1) == []


class TestProseCorrect:
    """定稿章的字词级订正通道。

    `PUT /prose` 一律拒 finalized（防重写），`human_rewrite` 要把章打回 generating
    （机检/审校/定稿全重跑）。结果定稿章里的错别字、旧称没有合规通道——
    真实书第 8/10 章因此留着「筑基修士」「练气一层」。
    """

    BODY = (
        "林寒把玄铁令按进凹槽，雨水顺着铁皮棚往下淌。远处的探照灯扫过来又扫过去，"
        "他压低身子贴着墙面挪了半步，指节因为用力而发白。"
        + "他把呼吸放到最缓，听着巷口的脚步近了又远。" * 3
    )

    def _patch(self, monkeypatch, body=None, status="finalized"):
        from ainovel import chapters as C
        from ainovel import statemachine as SM

        body = self.BODY if body is None else body
        written: dict = {}
        monkeypatch.setattr(SM, "current_status", lambda b, n: status)
        monkeypatch.setattr(SM, "set_current_version", lambda b, n, v: None)
        monkeypatch.setattr(C, "read_prose", lambda b, n: {
            "chapter_no": n, "version": 1, "status": "final",
            "word_count": len(body), "_body": body})
        monkeypatch.setattr(C, "write_prose", lambda b, n, fm, bd: written.update({"fm": fm, "body": bd}))
        monkeypatch.setattr(pipeline, "_next_version", lambda b, n: 2)
        return written

    def test_replaces_and_bumps_version(self, book_env, monkeypatch):
        book, _ = book_env
        written = self._patch(monkeypatch)
        r = pipeline.correct_prose(
            book, 1, [{"old": "玄铁令", "new": "铁牌"}], "统一术语")
        assert "铁牌" in written["body"] and "玄铁令" not in written["body"]
        assert written["fm"]["version"] == 2
        assert written["fm"]["status"] == "final"
        assert r["version"] == 2 and len(r["applied"]) == 1

    def test_old_must_appear_exactly_once(self, book_env, monkeypatch):
        book, _ = book_env
        self._patch(monkeypatch)
        with pytest.raises(pipeline.PipelineError, match="0 次"):
            pipeline.correct_prose(book, 1, [{"old": "不存在的一句话", "new": "x"}], "理由")
        with pytest.raises(pipeline.PipelineError, match="3 次"):
            # 「他把呼吸」出现 6 次，用它验证多次命中会被拒
            pipeline.correct_prose(book, 1, [{"old": "他把呼吸", "new": "他喘息"}], "理由")

    def test_reason_required(self, book_env, monkeypatch):
        book, _ = book_env
        self._patch(monkeypatch)
        with pytest.raises(pipeline.PipelineError, match="理由"):
            pipeline.correct_prose(book, 1, [{"old": "玄铁令", "new": "铁牌"}], "   ")

    def test_over_five_percent_rejected_as_rewrite(self, book_env, monkeypatch):
        book, _ = book_env
        self._patch(monkeypatch)
        with pytest.raises(pipeline.PipelineError, match="超过"):
            pipeline.correct_prose(book, 1, [{
                "old": "玄铁令按进凹槽，雨水顺着铁皮棚往下淌",
                "new": "铁牌按进凹槽，雨水顺着铁皮棚往下淌，冷风从巷口灌进来吹得他眼眶发酸",
            }], "重写")

    def test_banned_word_introduced_rejected(self, book_env, monkeypatch):
        book, _ = book_env
        self._patch(monkeypatch)
        with pytest.raises(pipeline.PipelineError, match="禁词"):
            pipeline.correct_prose(book, 1, [{"old": "凹槽", "new": "凹槽云雾缭绕"}], "试试")

    def test_only_finalized_allowed(self, book_env, monkeypatch):
        book, _ = book_env
        self._patch(monkeypatch, status="mech_check")
        from ainovel import chapters as C

        monkeypatch.setattr(C, "read_prose", lambda b, n: {
            "chapter_no": 1, "version": 1, "status": "draft", "_body": self.BODY})
        with pytest.raises(pipeline.PipelineError, match="尚未定稿"):
            pipeline.correct_prose(book, 1, [{"old": "玄铁令", "new": "铁牌"}], "理由")


