"""P1 稳定性回归测试（2026-10-26 第二批修复）。

覆盖五项「防护悄悄失效」或「技术债」类问题：
1. Bible 固定表格节的表头校验（列名漂移 → 境界倒退检测静默失效）
2. G6 细纲 plant 操作的 FO 编号格式校验
3. W2/W4/W5 场景专项也必须有字数区间约束
4. db.rebuild() 原地重建要按文件刷新 status（文件是真相源）
5. FastAPI lifespan 取代已弃用的 on_event

与 test_p0_regressions.py / test_p1_pipeline.py / test_p1_archive_api.py 分开：
那三个守「用户可感知的行为」，本文件守「不报错的失效」。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ainovel import api, bible, books, chapters, db, outline, pipeline, prompts, statemachine
from ainovel.api import app
from ainovel.mockllm import MockLLM

client = TestClient(app)


# ---------------------------------------------------------------------------
# 1. Bible 表头校验（规格01 §2.2）
# ---------------------------------------------------------------------------

class TestBibleHeaderValidation:
    """原实现 POWER_COLS/HIDDEN_COLS/TERM_COLS 三个常量定义后零引用，
    力量体系列名写错 → power_rows() 返回 [] → 境界倒退检测静默失效。"""

    GOOD_POWER = ("| 大境界 | 子境界 | 突破条件 | 能力边界(不能做什么) | 战力对标 | 寿元 | 代价与风险 | 突破异象 |\n"
                  "|---|---|---|---|---|---|---|---|\n"
                  "| 炼气 | 三层 | 聚气 | 不能御物 | 凡人 | 百岁 | 气海刺痛 | 无 |\n")

    def test_good_headers_pass(self):
        assert bible.validate_sections({"力量体系": self.GOOD_POWER}) == []

    def test_wrong_column_names_rejected(self):
        bad = ("| 境界 | 层次 | 条件 | 边界 | 战力 | 寿元 | 代价 | 异象 |\n"
               "|---|---|---|---|---|---|---|---|\n"
               "| 炼气 | 三层 | x | y | z | w | v | u |\n")
        problems = bible.validate_sections({"力量体系": bad})
        assert problems, "列名全错必须报错"
        assert "力量体系" in problems[0] and "大境界" in problems[0]

    def test_partial_column_mismatch_rejected(self):
        """只错一两列也要报——这类最隐蔽，下游照样静默失效。"""
        bad = ("| 大境界 | 子境界 | 突破条件 | 战力对标 | 寿元 | 代价与风险 | 突破异象 |\n"
               "|---|---|---|---|---|---|---|\n"
               "| 炼气 | 三层 | x | y | z | w | v |\n")
        problems = bible.validate_sections({"力量体系": bad})
        assert problems and "缺列" in problems[0] and "能力边界" in problems[0]

    def test_write_rejects_bad_headers(self, home):
        books.create_book("圣经书", genre_template="urban")
        with pytest.raises(bible.BibleError):
            bible.write("圣经书", {"力量体系": "| 错列 |\n|---|\n| x |\n"})
        # 磁盘未被破坏
        assert "炼气" not in bible.read("圣经书").get("力量体系", "")

    def test_empty_section_not_validated(self, home):
        """建书时的「（待生成）」空节不该被校验拦下。"""
        books.create_book("空节书", genre_template="urban")
        bible.write("空节书", {"力量体系": "（待生成）"})

    def test_non_table_section_not_validated(self, home):
        """非表格形态的节（如纯文字说明）不强行校验。"""
        books.create_book("文字书", genre_template="urban")
        assert bible.validate_sections({"力量体系": "炼气期分九层，每层需聚气三周。"}) == []

    def test_hidden_and_term_columns_validated(self):
        assert bible.validate_sections({"隐藏规则": "| 规则 | 等级 |\n|---|---|\n| x | y |"})
        assert bible.validate_sections({"术语表": "| 词 | 义 |\n|---|---|\n| x | y |"})

    def test_parenthetical_header_suffix_accepted(self):
        """列名带括号补充是合法写法（真实书 `隐藏等级(低/中/高)`），不能被判成缺列。

        修复前：整本 STORY_BIBLE 保存被拦死——用户改任何一节都会撞「隐藏规则表头缺列」，
        而文件本身从来没坏过。校验目标应是"列名漂移到别的语义"，不是"写法不一致"。
        """
        ok = {"隐藏规则": "| 规则内容 | 隐藏等级(低/中/高) | 计划揭晓章节 |\n|---|---|---|\n| x | 高 | 80 |"}
        assert bible.validate_sections(ok) == []
        # 全角括号同样接受
        ok2 = {"力量体系": "| 大境界 | 子境界 | 突破条件 | 能力边界（不能做什么） | 战力对标 | 寿元 | 代价与风险 | 突破异象 |\n"
                          "|---|---|---|---|---|---|---|---|\n| 炼气 | 三层 | x | y | z | w | v | u |"}
        assert bible.validate_sections(ok2) == []

    def test_real_drift_still_rejected_after_normalization(self):
        """归一化不能把真漂移一起放过：`隐藏级别` 语义已变，必须继续报错。"""
        bad = {"隐藏规则": "| 规则内容 | 隐藏级别 | 计划揭晓章节 |\n|---|---|---|\n| x | 高 | 80 |"}
        problems = bible.validate_sections(bad)
        assert problems and "隐藏等级" in problems[0]


# ---------------------------------------------------------------------------
# 2. G6 plant FO 编号校验（规格02 P-G6 程序校验）
# ---------------------------------------------------------------------------

class TestPlantForeshadowValidation:
    """原实现 plant 直接 continue，任意字符串都能入台账。"""

    def _contract(self, fo_id: str, op: str = "plant") -> dict:
        return {
            "chapter_no": 1, "volume": 1, "stage": "引入", "emotion_task": "压",
            "core_event": "事件", "scenes": [], "emotion_arc": {},
            "hook": {"content": "钩子", "type": "悬念"},
            "foreshadow_ops": [{"op": op, "fo_id": fo_id, "note": ""}],
            "word_range": [2200, 2800], "pov": "林寒",
        }

    def test_plant_rejects_malformed_id(self, book_env):
        book, _ = book_env
        with pytest.raises(pipeline.PipelineError) as ei:
            pipeline._validate_contract_ops(self._contract("随便写的编号"), book, "")
        assert "FO-xxx" in str(ei.value)

    def test_plant_accepts_wellformed_id(self, book_env):
        book, _ = book_env
        pipeline._validate_contract_ops(self._contract("FO-004"), book, "")

    def test_plant_rejects_duplicate_id(self, book_env):
        book, _ = book_env
        outline.write_plot_points(book, [
            {"编号": "FO-004", "内容": "已有", "埋设章": "1", "推进章": "",
             "预计回收章": "", "状态": "planted", "备注": ""}])
        with pytest.raises(pipeline.PipelineError) as ei:
            pipeline._validate_contract_ops(self._contract("FO-004"), book, "")
        assert "重复埋设" in str(ei.value)

    def test_advance_still_requires_existing_id(self, book_env):
        book, _ = book_env
        with pytest.raises(pipeline.PipelineError):
            pipeline._validate_contract_ops(self._contract("FO-999", "advance"), book, "")


# ---------------------------------------------------------------------------
# 3. W2/W4/W5 场景专项的字数区间约束（规格03 §2.3）
# ---------------------------------------------------------------------------

class TestSceneSpecialWordRange:
    """原实现只给 W1/W3 传 〖字数区间〗，三个场景专项被当作整章调用时
    没有任何字数约束，只能靠 M-01 事后兜底。"""

    PROSE_IDS = ("W1", "W2", "W3", "W4", "W5")

    def test_all_prose_prompts_declare_word_range(self):
        for pid in self.PROSE_IDS:
            p = prompts.get(pid)
            assert "字数区间" in p.fm.get("inputs", []), f"{pid} 的 inputs 缺 字数区间"
            assert "〖字数区间〗" in p.full_text(), f"{pid} 正文缺 〖字数区间〗 占位"

    def test_all_prose_prompts_render_with_word_range(self, home):
        """模拟 pipeline 传参，确认五个提示词都能渲染且无残留占位。"""
        base = {"系统段": "S", "章合同": "C", "装配上下文": "X", "字数区间": "2200-2800"}
        extra = {
            "W2": {"本场景人物状态": "a", "本场景潜台词要求": "b"},
            "W4": {"力量体系相关行": "a", "突破代价设定": "b"},
            "W5": {"对抗双方状态与底牌": "a", "已埋伏笔清单": "b"},
            "W1": {"范文样本": "a", "黄金三章附加要求": "",
                   "目标字数": "2500", "开篇承接点": "从上一章结尾的荒滩起身"},
            "W3": {"章号": "1"},
        }
        for pid in self.PROSE_IDS:
            _, text = prompts.render(pid, {**base, **extra[pid]})
            assert "〖" not in text, f"{pid} 渲染后残留占位"
            assert "2200-2800" in text, f"{pid} 渲染结果里没有字数区间"

    def test_pipeline_passes_word_range_to_scene_specials(self, book_env):
        """端到端：用 W2 生成也不得因缺字数区间而崩在渲染层。"""
        book, _ = book_env
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)
        r = pipeline.generate_prose(book, 1, client=mock, prompt_id="W2")
        assert r["word_count"] > 0


# ---------------------------------------------------------------------------
# 4. rebuild 按文件刷新 status（规格01 §1：文件是真相源）
# ---------------------------------------------------------------------------

class TestRebuildRefreshesStatus:
    """原实现 ON CONFLICT 不更新 status，UI 那个「从文件重建索引」按钮
    重建后已污染的状态不会被修（真删 .db 后重建反而是对的）。"""

    def test_rebuild_overwrites_stale_status(self, home):
        books.create_book("重建书", genre_template="urban")
        outline.write_outline(
            "重建书",
            {"logline": "L", "ending_direction": "", "volumes": []},
            rows=[{"章": "1", "卷": "1", "本章目标": "t", "冲突来源": "c",
                   "爽点/揭露": "s", "钩子类型": "悬念", "重要度★": "★3"}],
            rhythm="r",
        )
        mock = MockLLM()
        pipeline.generate_outline("重建书", 1, client=mock)
        pipeline.confirm_outline("重建书", 1)
        assert statemachine.current_status("重建书", 1) == "outline_locked"

        # 人为把 DB 状态改坏（模拟"已污染"）
        with db.tx() as conn:
            conn.execute(
                "UPDATE chapters SET status='finalized' WHERE book_id=? AND no=1",
                (books.book_id("重建书"),))
        assert statemachine.current_status("重建书", 1) == "finalized"

        # rebuild 后应按文件真相恢复（细纲已锁定、无正文 → outline_locked）
        db.rebuild()
        assert statemachine.current_status("重建书", 1) == "outline_locked"


# ---------------------------------------------------------------------------
# 5. FastAPI lifespan（技术债：on_event 已弃用）
# ---------------------------------------------------------------------------

class TestLifespanInsteadOfOnEvent:
    def test_no_deprecated_on_event(self):
        src = (api.__file__ or "").replace(".pyc", ".py")
        with open(src, encoding="utf-8") as f:
            lines = f.read().splitlines()
        # 查真实的装饰器用法（行首），docstring 里的说明性文字不算
        offenders = [ln for ln in lines if ln.strip().startswith("@app.on_event")]
        assert not offenders, f"应使用 lifespan，on_event 已被 FastAPI 弃用: {offenders}"

    def test_lifespan_creates_settings_file(self, home):
        """启动钩子仍要保证 data/settings.json 存在（原行为不能丢）。"""
        from ainovel import settings
        from ainovel.paths import data_dir

        settings.load()
        assert (data_dir() / "settings.json").exists()


# ---------------------------------------------------------------------------
# 6. 境界序列要剥 markdown 强调符（真实书把大境界写成 **感气境**）
# ---------------------------------------------------------------------------

class TestRealmOrderStripsMarkdown:
    """真实书的「力量体系」大境界列普遍写成 `**感气境**`。

    realm_order() 原样取值 → 序列里全是带星号的词条 → `感气境` 整体匹配不上
    → characters._realm_index 返回 None → 境界倒退检测静默跳过（不报错的失效）。
    """

    POWER = ("| 大境界 | 子境界 | 突破条件 | 能力边界(不能做什么) | 战力对标 | 寿元 | 代价与风险 | 突破异象 |\n"
             "|---|---|---|---|---|---|---|---|\n"
             "| **感气境** | 初入、洗髓 | 引气入体 | 不能离体 | 特种兵 | 百岁 | 经脉撕裂 | 渗油脂 |\n"
             "| **凝煞境** | 淬骨 | 结煞 | 不能抗重火力 | 破小队 | 120岁 | 煞气攻心 | 霜降 |\n")

    def test_bold_realm_names_clean(self, home):
        books.create_book("境界书", genre_template="urban")
        bible.update_section("境界书", "力量体系", self.POWER)
        order = bible.realm_order("境界书")
        assert "感气境" in order, order
        assert "凝煞境" in order, order
        assert not [x for x in order if "*" in x], order

    def test_realm_index_matches_in_characters(self, home):
        """characters 侧的索引函数必须能在同一序列上定位到大境界。"""
        from ainovel.characters import _realm_index

        books.create_book("境界书2", genre_template="urban")
        bible.update_section("境界书2", "力量体系", self.POWER)
        order = bible.realm_order("境界书2")
        assert _realm_index("感气境", order) is not None
        assert _realm_index("感气境初入", order) is not None
        assert _realm_index("凝煞境淬骨", order) > _realm_index("感气境初入", order)



class TestCostPrecision:
    """成本曾经恒为 0，而且是"看起来有值（0）"的静默失效。

    两重原因叠加：①按整数分记账；②每次调用各自 round。单次调用约 7.7k 输入
    + 4.4k 输出只值 0.09 分，每次都抹成 0，整章 19 次调用累计 1.68 分全丢。
    结果：成本汇总恒 0、「单章 >1 元标黄」验收线永不触发。
    """

    MODEL = "gemini-3.8-flash-high"

    def test_single_call_not_rounded_to_zero(self, home):
        from ainovel import settings

        mc = settings.cost_millicents(self.MODEL, 7700, 4400)
        assert mc > 0, "一次常规调用的成本不能被抹成 0"
        # 分口径本来就是 0——这正是不能再用它记账的原因
        assert settings.cost_cents(self.MODEL, 7700, 4400) == 0

    def test_chapter_summary_keeps_precision(self, book_env):
        from ainovel import books as B
        from ainovel import db, settings

        book, _ = book_env
        bid = B.book_id(book)
        db.log_generation(book_id=bid, chapter_no=1, stage="prose", model=self.MODEL,
                          input_tokens=7700, output_tokens=4400,
                          cost_millicents=settings.cost_millicents(self.MODEL, 7700, 4400))
        s = db.cost_summary(bid, 1)
        assert s["calls"] == 1
        assert s["cost_millicents"] > 0, s
        assert s["cost_cents"] == 0, "1 分不到的章在分口径下仍应是 0，毫分口径才看得见"

    def test_backfill_recomputes_legacy_rows(self, book_env):
        from ainovel import books as B
        from ainovel import db

        book, _ = book_env
        bid = B.book_id(book)
        with db.tx() as conn:
            conn.execute(
                "INSERT INTO generation_log (book_id, chapter_no, stage, model, input_tokens,"
                " output_tokens, cost_cents, cost_millicents, status, created_at)"
                " VALUES (?,?,?,?,?,?,?,NULL,?,?)",
                (bid, 2, "prose", self.MODEL, 7700, 4400, 0, "ok", db.now()),
            )
        assert db.backfill_cost_millicents() >= 1
        assert db.cost_summary(bid, 2)["cost_millicents"] > 0

    def test_backfill_leaves_unknown_price_null(self, book_env):
        """查不到单价就保持 NULL，不能回填成 0——那是把「不会算」固化成「没花钱」。"""
        from ainovel import books as B
        from ainovel import db

        book, _ = book_env
        bid = B.book_id(book)
        with db.tx() as conn:
            conn.execute(
                "INSERT INTO generation_log (book_id, chapter_no, stage, model, input_tokens,"
                " output_tokens, cost_cents, cost_millicents, status, created_at)"
                " VALUES (?,?,?,?,?,?,?,NULL,?,?)",
                (bid, 3, "prose", "some-unknown-model", 7700, 4400, 0, "ok", db.now()),
            )
        db.backfill_cost_millicents()
        with db.tx() as conn:
            row = conn.execute(
                "SELECT cost_millicents FROM generation_log WHERE book_id=? AND chapter_no=3",
                (bid,)).fetchone()
        assert row["cost_millicents"] is None


class TestCostPrecision:
    """成本曾经恒为 0，而且是"看起来有值（0）"的静默失效。

    两重原因叠加：①按整数分记账；②每次调用各自 round。单次调用约 7.7k 输入
    + 4.4k 输出只值 0.09 分，每次都抹成 0，整章 19 次调用累计 1.68 分全丢。
    结果：成本汇总恒 0、「单章 >1 元标黄」验收线永不触发。
    """

    MODEL = "gemini-3.8-flash-high"

    def test_single_call_not_rounded_to_zero(self, home):
        from ainovel import settings

        mc = settings.cost_millicents(self.MODEL, 7700, 4400)
        assert mc > 0, "一次常规调用的成本不能被抹成 0"
        # 分口径本来就是 0——这正是不能再用它记账的原因
        assert settings.cost_cents(self.MODEL, 7700, 4400) == 0

    def test_chapter_summary_keeps_precision(self, book_env):
        from ainovel import books as B
        from ainovel import db, settings

        book, _ = book_env
        bid = B.book_id(book)
        db.log_generation(book_id=bid, chapter_no=1, stage="prose", model=self.MODEL,
                          input_tokens=7700, output_tokens=4400,
                          cost_millicents=settings.cost_millicents(self.MODEL, 7700, 4400))
        s = db.cost_summary(bid, 1)
        assert s["calls"] == 1
        assert s["cost_millicents"] > 0, s
        assert s["cost_cents"] == 0, "1 分不到的章在分口径下仍应是 0，毫分口径才看得见"

    def test_backfill_recomputes_legacy_rows(self, book_env):
        from ainovel import books as B
        from ainovel import db

        book, _ = book_env
        bid = B.book_id(book)
        with db.tx() as conn:
            conn.execute(
                "INSERT INTO generation_log (book_id, chapter_no, stage, model, input_tokens,"
                " output_tokens, cost_cents, cost_millicents, status, created_at)"
                " VALUES (?,?,?,?,?,?,?,NULL,?,?)",
                (bid, 2, "prose", self.MODEL, 7700, 4400, 0, "ok", db.now()),
            )
        assert db.backfill_cost_millicents() >= 1
        assert db.cost_summary(bid, 2)["cost_millicents"] > 0

    def test_backfill_leaves_unknown_price_null(self, book_env):
        """查不到单价就保持 NULL，不能回填成 0——那是把「不会算」固化成「没花钱」。"""
        from ainovel import books as B
        from ainovel import db

        book, _ = book_env
        bid = B.book_id(book)
        with db.tx() as conn:
            conn.execute(
                "INSERT INTO generation_log (book_id, chapter_no, stage, model, input_tokens,"
                " output_tokens, cost_cents, cost_millicents, status, created_at)"
                " VALUES (?,?,?,?,?,?,?,NULL,?,?)",
                (bid, 3, "prose", "some-unknown-model", 7700, 4400, 0, "ok", db.now()),
            )
        db.backfill_cost_millicents()
        with db.tx() as conn:
            row = conn.execute(
                "SELECT cost_millicents FROM generation_log WHERE book_id=? AND chapter_no=3",
                (bid,)).fetchone()
        assert row["cost_millicents"] is None
