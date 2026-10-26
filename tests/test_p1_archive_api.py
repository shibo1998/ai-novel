"""P1 API 契约与档案层回归测试（2026-10-26 审计修复）。

覆盖 6 项：
1. G5 采纳改为合并式，不覆盖伏笔台账；logline/ending_direction 不被建书占位串覆盖
2. G4 关系网真正写回已有角色的「关系网络」H2 节
3. PUT /chapters/{no}/prose 人工手改正文（finalized 拒绝）
4. GET /chapters/{no}/snapshots/{version} 快照正文回看
5. PUT /locations 地点档案可编辑（含 outline.write_locations）
6. POST /export/md 导出 Markdown 合并版

全部走 conftest.py 的 home / book_env fixture（临时 AINOVEL_HOME）+ MockLLM，
不读写真实 books/、data/，不联网。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ainovel import books, chapters, characters, outline, pipeline, statemachine
from ainovel import frontmatter as FM
from ainovel.api import app
from ainovel.mockllm import MockLLM

client = TestClient(app)


# ---------------------------------------------------------------------------
# 1. G5 采纳：合并式伏笔台账 + logline/ending_direction 保留
# ---------------------------------------------------------------------------

G5_HEAD = """## 卷规划
| 卷号 | 卷名 | 起止章 | 核心冲突 | 本卷目标 | 卷末高潮 | 卷末钩子(出卷悬念) |
|---|---|---|---|---|---|---|
| 1 | 坊市 | 1-12 | 坊市争夺 | 立足 | 乱葬岗对决 | 身世线索 |

## 章节总表
| 章 | 卷 | 本章目标 | 冲突来源 | 爽点/揭露 | 钩子类型 | 重要度★ |
|---|---|---|---|---|---|---|
"""


def _g5_text(fo_rows: list[str], chapters_n: int = 12) -> str:
    """拼一份 G5 形态输出：卷规划 + 章节总表 + 指定伏笔行 + 节奏总谱。"""
    lines = [G5_HEAD]
    for i in range(1, chapters_n + 1):
        lines.append(f"| {i} | 1 | 新目标{i} | 新冲突{i} | 新爽点{i} | 悬念 | ★3 |\n")
    lines.append("\n## 伏笔总表\n")
    lines.append("| 编号 | 内容 | 埋设章 | 推进章 | 预计回收章 | 备注 |\n")
    lines.append("|---|---|---|---|---|---|\n")
    lines.extend(fo_rows)
    lines.append("\n## 爽点节奏总谱\n\n新节奏：1-2 小打脸，12 卷终。\n")
    return "".join(lines)


class TestAdoptOutlineMergesForeshadows:
    """原实现全量覆写台账：building 状态被重置为 planted、未提及的 FO 行被删除、
    编号从 FO-001 重新编（规格02 §4 末句"AI 永不静默覆盖人工内容"）。"""

    def _seed(self, book: str) -> None:
        """已有台账：FO-001 已推进（building）、FO-002 已回收（paid）。"""
        outline.write_plot_points(book, [
            {"编号": "FO-001", "内容": "玄铁令发热", "埋设章": "1", "推进章": "7",
             "预计回收章": "15", "状态": "building", "备注": "已推进"},
            {"编号": "FO-002", "内容": "老周隐瞒身世", "埋设章": "2", "推进章": "5",
             "预计回收章": "9", "状态": "paid", "备注": "已回收"},
        ])

    def test_existing_status_and_id_preserved(self, book_env):
        book, _ = book_env
        self._seed(book)
        # G5 输出里 FO-001 的内容与台账一致（但编号写成 F1）
        text = _g5_text(["| F1 | 玄铁令发热 | 1 |  | 15 | 跨卷 |\n"])
        r = client.post(f"/api/books/{book}/adopt/outline", json={"content": text})
        assert r.status_code == 200
        body = r.json()
        assert body["foreshadows_added"] == 0
        assert body["foreshadows_kept"] == 1
        assert body["id_map"]["F1"] == "FO-001"

        rows = {x["编号"]: x for x in outline.read_plot_points(book)}
        assert rows["FO-001"]["状态"] == "building"     # 状态没被重置成 planted
        assert rows["FO-001"]["推进章"] == "7"          # 推进章没被清空
        assert rows["FO-002"]["状态"] == "paid"         # 未提及的行没被删除

    def test_new_content_gets_next_number_not_reuse(self, book_env):
        book, _ = book_env
        self._seed(book)
        text = _g5_text(["| F1 | 玄铁令发热 | 1 |  | 15 |  |\n",
                         "| F2 | 父亲留下的信 | 3 |  | 20 |  |\n"])
        r = client.post(f"/api/books/{book}/adopt/outline", json={"content": text}).json()
        assert r["foreshadows_added"] == 1 and r["foreshadows_kept"] == 1
        # 新内容拿 FO-003（max 现有编号 +1），不复用 FO-001/FO-002
        assert r["id_map"]["F2"] == "FO-003"

        rows = {x["编号"]: x for x in outline.read_plot_points(book)}
        assert rows["FO-003"]["内容"] == "父亲留下的信"
        assert rows["FO-003"]["状态"] == "planted"
        assert rows["FO-001"]["内容"] == "玄铁令发热"   # 原内容没被新内容顶替
        assert len(rows) == 3

    def test_empty_existing_ledger_starts_at_fo_001(self, home):
        """全新书（台账为空）时仍从 FO-001 起编，保持原有行为。"""
        name = "空台账书"
        books.create_book(name, genre_template="urban")
        text = _g5_text(["| F1 | 第一条 | 1 |  | 9 |  |\n"])
        r = client.post(f"/api/books/{name}/adopt/outline", json={"content": text}).json()
        assert r["id_map"]["F1"] == "FO-001"
        assert outline.read_plot_points(name)[0]["内容"] == "第一条"

    def test_repeated_adopt_is_idempotent_for_foreshadows(self, book_env):
        """同样内容连续采纳两次不得重复新增（否则台账会无限膨胀）。"""
        book, _ = book_env
        self._seed(book)
        text = _g5_text(["| F1 | 玄铁令发热 | 1 |  | 15 |  |\n",
                         "| F2 | 父亲留下的信 | 3 |  | 20 |  |\n"])
        first = client.post(f"/api/books/{book}/adopt/outline", json={"content": text}).json()
        second = client.post(f"/api/books/{book}/adopt/outline", json={"content": text}).json()
        assert first["foreshadows_added"] == 1
        assert second["foreshadows_added"] == 0
        assert second["foreshadows_kept"] == 2
        assert len(outline.read_plot_points(book)) == 3

    def test_logline_and_ending_direction_not_overwritten(self, book_env):
        """原实现用 meta 正文（建书占位串）覆盖 logline，ending_direction 被清空。"""
        book, _ = book_env
        outline.write_outline(
            book,
            {"logline": "用户手写的一句话故事", "ending_direction": "主角牺牲小我",
             "volumes": []},
            rows=outline.read_outline(book)["chapter_rows"], rhythm="原节奏")
        client.post(f"/api/books/{book}/adopt/outline",
                    json={"content": _g5_text([])})
        fm = outline.read_outline(book)
        assert fm["logline"] == "用户手写的一句话故事"
        assert fm["ending_direction"] == "主角牺牲小我"

    def test_logline_falls_back_to_meta_body_when_empty(self, home):
        """OUTLINE.md 的 logline 为空时才用 meta 正文兜底（原有兜底行为保留）。"""
        name = "空logline书"
        books.create_book(name, genre_template="urban")
        # 补章节总表行，否则 G5 采纳会因"未解析到有效行"被拒
        outline.write_outline(name, {"logline": "", "ending_direction": "", "volumes": []},
                             rows=[{"章": "1", "卷": "1", "本章目标": "t", "冲突来源": "c",
                                    "爽点/揭露": "s", "钩子类型": "悬念", "重要度★": "★3"}])
        client.post(f"/api/books/{name}/adopt/outline", json={"content": _g5_text([])})
        logline = outline.read_outline(name)["logline"]
        assert "一句话故事核" in logline  # meta 建书占位串兜底

    def test_rhythm_and_chapter_rows_still_written(self, book_env):
        """合并不得影响 G5 对章节总表与节奏总谱的正常写入。"""
        book, _ = book_env
        client.post(f"/api/books/{book}/adopt/outline", json={"content": _g5_text([])})
        od = outline.read_outline(book)
        assert len(od["chapter_rows"]) == 12
        assert od["chapter_rows"][0]["本章目标"] == "新目标1"
        assert "新节奏" in outline.read_rhythm(book)


# ---------------------------------------------------------------------------
# 2. G4 关系网：合并写回已有角色的「关系网络」H2 节
# ---------------------------------------------------------------------------

G4_TEXT = """===角色:林寒===
## 关系网络
| 对象 | 关系 | 不稳定因素 |
|---|---|---|
| 老周 | 师徒 | 老周隐瞒身世 |

===角色:沈九===
## 关系网络
| 对象 | 关系 | 不稳定因素 |
|---|---|---|
| 林寒 | 宿敌 | 旧仇未清 |
"""


class TestAdoptCharactersMergesRelationships:
    """原实现对已存在角色 `continue` 直接丢弃关系行；新角色写进正文却无
    `## 关系网络` 节标题 → characters.relationships() 恒为空。"""

    def test_existing_character_relationships_written(self, book_env):
        book, _ = book_env
        r = client.post(f"/api/books/{book}/adopt/characters",
                        json={"content": G4_TEXT}).json()
        assert r["ok"] and r["created"] == ["沈九"]
        assert "林寒" in r["updated"]

        rels = characters.relationships(book, "林寒")
        assert {"对象": "老周", "关系": "师徒", "不稳定因素": "老周隐瞒身世"} in rels
        # 已有角色卡不得被整体覆盖：name/role 仍在，其他 H2 节也保留
        d = characters.read(book, "林寒")
        assert d["name"] == "林寒" and d["role"] == "主角"
        assert "## 表层信息" in d["_body"]
        assert {"林寒", "老周", "沈九"} <= set(characters.list_characters(book))

    def test_new_character_has_relationship_section(self, book_env):
        book, _ = book_env
        client.post(f"/api/books/{book}/adopt/characters", json={"content": G4_TEXT})
        rels = characters.relationships(book, "沈九")
        assert {"对象": "林寒", "关系": "宿敌", "不稳定因素": "旧仇未清"} in rels

    def test_relationships_are_deduplicated(self, book_env):
        book, _ = book_env
        client.post(f"/api/books/{book}/adopt/characters", json={"content": G4_TEXT})
        client.post(f"/api/books/{book}/adopt/characters", json={"content": G4_TEXT})
        rels = characters.relationships(book, "林寒")
        assert sum(1 for x in rels if x["对象"] == "老周") == 1

    def test_existing_relationship_rows_not_overwritten(self, book_env):
        """人工已填的关系行不被 AI 改掉（AI 永不静默覆盖人工内容）。"""
        book, _ = book_env
        characters.merge_relationships(book, "林寒", [
            {"对象": "老周", "关系": "人工填的师徒", "不稳定因素": "人工判断"}])
        client.post(f"/api/books/{book}/adopt/characters", json={"content": G4_TEXT})
        rels = characters.relationships(book, "林寒")
        row = next(x for x in rels if x["对象"] == "老周")
        assert row["关系"] == "人工填的师徒"

    def test_merge_into_card_without_section(self, home):
        """角色卡没有 `## 关系网络` 节时补建该节（原缺陷：写进顶层读不出）。"""
        name = "缺节书"
        books.create_book(name, genre_template="urban")
        characters.create(name, "林寒", role="主角")
        d = characters.read(name, "林寒")
        fm = {k: v for k, v in d.items() if not k.startswith("_")}
        characters.write(name, fm, "## 表层信息\n\n只有这一节\n")

        added = characters.merge_relationships(name, "林寒", [
            {"对象": "老周", "关系": "师徒", "不稳定因素": ""}])
        assert added == 1
        assert characters.relationships(name, "林寒")[0]["对象"] == "老周"
        # 原有内容保留
        assert "只有这一节" in characters.read(name, "林寒")["_body"]

    def test_merge_into_placeholder_section(self, home):
        """节内只有「（待填）」占位时用表格替换占位。"""
        name = "占位书"
        books.create_book(name, genre_template="urban")
        characters.create(name, "林寒", role="主角")  # 建卡时关系网络节即「（待填）」
        assert characters.relationships(name, "林寒") == []
        characters.merge_relationships(name, "林寒", [
            {"对象": "老周", "关系": "师徒", "不稳定因素": "隐瞒"}])
        rels = characters.relationships(name, "林寒")
        assert rels == [{"对象": "老周", "关系": "师徒", "不稳定因素": "隐瞒"}]

    def test_relationship_matrix_rows_parsed(self, book_env):
        """G4「关系矩阵总览表」形态（对象A/对象B 六列）也要能写回。"""
        book, _ = book_env
        text = """===角色:林寒===
| 对象A | 对象B | 关系 | 渊源/恩怨 | 张力点（稳定/暗流/将转变） | 真实态度(A→B) |
|---|---|---|---|---|---|
| 林寒 | 老周 | 表面师徒 | 老周曾救他 | 将转变 | 依赖 |
"""
        client.post(f"/api/books/{book}/adopt/characters", json={"content": text})
        rels = characters.relationships(book, "林寒")
        row = next(x for x in rels if x["对象"] == "老周")
        assert row["关系"] == "表面师徒"
        assert row["不稳定因素"] == "将转变"

    def test_both_table_shapes_in_one_card(self, book_env):
        """同一张分卡里既有三列关系表又有六列矩阵：两种都要解析到，不能互相吞掉。"""
        book, _ = book_env
        text = """===角色:林寒===
## 关系网络
| 对象 | 关系 | 不稳定因素 |
|---|---|---|
| 老周 | 师徒 | 隐瞒身世 |

## 关系矩阵（总览）
| 对象A | 对象B | 关系 | 渊源/恩怨 | 张力点（稳定/暗流/将转变） | 真实态度(A→B) |
|---|---|---|---|---|---|
| 林寒 | 沈九 | 宿敌 | 旧仇 | 将转变 | 忌惮 |
"""
        client.post(f"/api/books/{book}/adopt/characters", json={"content": text})
        rels = {x["对象"]: x for x in characters.relationships(book, "林寒")}
        assert rels["老周"]["关系"] == "师徒"
        assert rels["沈九"]["关系"] == "宿敌"


# ---------------------------------------------------------------------------
# 3. PUT /chapters/{no}/prose —— 人工手改正文（规格03 §2.5 三选项之一）
# ---------------------------------------------------------------------------

def _to_mech_check(book: str, mock: MockLLM, no: int = 1) -> None:
    pipeline.generate_outline(book, no, client=mock)
    pipeline.confirm_outline(book, no)
    pipeline.generate_prose(book, no, client=mock)
    assert statemachine.current_status(book, no) == "mech_check"


class TestProseEdit:
    """原实现全仓库无写正文的 API，人工关三选项缺「手改正文」。"""

    def test_edit_prose_updates_body_and_word_count(self, book_env):
        book, _ = book_env
        _to_mech_check(book, MockLLM())
        before = chapters.read_prose(book, 1)
        new_text = "他站在雨里。令牌烫了一下。\n他停住脚。"
        r = client.put(f"/api/books/{book}/chapters/1/prose",
                       json={"content": new_text})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] and body["word_count"] == len("他站在雨里。令牌烫了一下。他停住脚。")

        after = chapters.read_prose(book, 1)
        assert after["_body"] == new_text
        assert after["word_count"] == body["word_count"]
        assert after["status"] == "draft"
        assert after["version"] == before["version"]   # version 不变

    def test_edit_prose_rejected_after_finalize(self, book_env):
        book, _ = book_env
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)
        pipeline.generate_prose(book, 1, client=mock)
        pipeline.run_mech_check(book, 1, client=mock)
        pipeline.run_llm_review(book, 1, client=mock)
        pipeline.finalize(book, 1, client=mock)
        assert statemachine.current_status(book, 1) == "finalized"
        before = chapters.read_prose(book, 1)["_body"]

        r = client.put(f"/api/books/{book}/chapters/1/prose", json={"content": "篡改"})
        assert r.status_code == 400
        assert "已定稿" in r.json()["detail"]
        assert chapters.read_prose(book, 1)["_body"] == before  # 未被改动

    def test_edit_prose_rejected_without_prose(self, book_env):
        """细纲还没生成/未生成正文时无正文可改 → 明确拒绝而不是造出空正文。"""
        book, _ = book_env
        r = client.put(f"/api/books/{book}/chapters/1/prose", json={"content": "x"})
        assert r.status_code == 404

    def test_edit_prose_from_pending_finalize_requires_recheck(self, book_env):
        """在 pending_finalize 态手改正文后必须回退到 mech_check 重新过机检。

        否则用户改完正文就能直接定稿，M-01~M-11 等于被绕过（P1 审计）。
        """
        book, _ = book_env
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)
        pipeline.generate_prose(book, 1, client=mock)
        pipeline.run_mech_check(book, 1, client=mock)
        pipeline.run_llm_review(book, 1, client=mock)
        assert statemachine.current_status(book, 1) == "pending_finalize"

        r = client.put(f"/api/books/{book}/chapters/1/prose",
                       json={"content": "人工改写后的正文" * 300})
        assert r.status_code == 200
        # 关键断言：不能停在 pending_finalize，必须回到 mech_check 重跑机检
        assert r.json()["status"] == "mech_check"
        assert statemachine.current_status(book, 1) == "mech_check"
        # 而且此时不能直接定稿
        with pytest.raises(statemachine.StateError):
            pipeline.finalize(book, 1, client=mock)

    def test_unlock_allows_rework_after_finalize(self, book_env):
        """定稿返工：unlock 让 finalized → pending_finalize，之后可手改。

        原来状态机有 unlock 边、报错文案也让用户"先解锁"，但没有路由也没有按钮，
        用户被指向一个不存在的地方（P1 审计：定稿返工是死路）。
        """
        book, _ = book_env
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)
        pipeline.generate_prose(book, 1, client=mock)
        pipeline.run_mech_check(book, 1, client=mock)
        pipeline.run_llm_review(book, 1, client=mock)
        pipeline.finalize(book, 1, client=mock)
        assert statemachine.current_status(book, 1) == "finalized"
        original = chapters.read_prose(book, 1)["_body"]

        # 定稿后手改被拒
        r = client.put(f"/api/books/{book}/chapters/1/prose", json={"content": "x" * 500})
        assert r.status_code == 400

        # 解锁后即可手改
        u = client.post(f"/api/books/{book}/chapters/1/unlock")
        assert u.status_code == 200
        assert u.json()["status"] == "pending_finalize"
        assert statemachine.current_status(book, 1) == "pending_finalize"

        r2 = client.put(f"/api/books/{book}/chapters/1/prose", json={"content": "返工后的正文" * 300})
        assert r2.status_code == 200
        assert chapters.read_prose(book, 1)["_body"] != original

    def test_unlock_rejects_non_finalized(self, book_env):
        """未定稿的章节不需要解锁（避免误操作把 pending_finalize 打回去）。"""
        book, _ = book_env
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)
        pipeline.generate_prose(book, 1, client=mock)
        pipeline.run_mech_check(book, 1, client=mock)
        assert statemachine.current_status(book, 1) == "llm_review"
        r = client.post(f"/api/books/{book}/chapters/1/unlock")
        assert r.status_code == 400

    def test_edit_prose_keeps_other_frontmatter(self, book_env):
        """只改正文与 word_count，chapter_no/version/finalized_at 等保持原样。"""
        book, _ = book_env
        _to_mech_check(book, MockLLM())
        before = chapters.read_prose(book, 1)
        client.put(f"/api/books/{book}/chapters/1/prose", json={"content": "新正文内容"})
        after = chapters.read_prose(book, 1)
        for key in ("chapter_no", "version", "finalized_at"):
            assert after[key] == before[key]
        fm, _body = FM.read_file(chapters.prose_path(book, 1))
        assert "_body" not in fm


# ---------------------------------------------------------------------------
# 4. GET /chapters/{no}/snapshots/{version} —— 快照正文回看
# ---------------------------------------------------------------------------

class TestSnapshotRead:
    """/versions 只返回元信息，原实现没有取快照正文的路由，用户看不到历史版本。"""

    def _finalized(self, book: str) -> MockLLM:
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)
        pipeline.generate_prose(book, 1, client=mock)
        pipeline.run_mech_check(book, 1, client=mock)
        pipeline.run_llm_review(book, 1, client=mock)
        pipeline.finalize(book, 1, client=mock)
        return mock

    def test_snapshot_content_returned(self, book_env):
        book, _ = book_env
        self._finalized(book)
        listed = client.get(f"/api/books/{book}/chapters/1/versions").json()
        assert listed, "定稿后应至少有 1 个快照"
        v = listed[0]["version"]

        r = client.get(f"/api/books/{book}/chapters/1/snapshots/{v}")
        assert r.status_code == 200
        data = r.json()
        assert data["version"] == v
        assert data["content"] == pipeline.get_snapshot(book, 1, v)
        assert data["content"] == chapters.read_prose(book, 1)["_body"]
        assert data["label"] and data["created_at"]   # 元信息来自 /versions

    def test_missing_snapshot_404(self, book_env):
        book, _ = book_env
        self._finalized(book)
        r = client.get(f"/api/books/{book}/chapters/1/snapshots/99")
        assert r.status_code == 404

    def test_snapshot_survives_later_edits(self, book_env):
        """快照是不可变历史：手改当前正文后回看旧版本仍应拿到定稿时的正文。"""
        book, _ = book_env
        self._finalized(book)
        snap = client.get(f"/api/books/{book}/chapters/1/snapshots/1").json()["content"]
        assert snap
        # 直接改盘上正文（模拟后续返工），快照不受影响
        prose = chapters.read_prose(book, 1)
        fm = {k: v for k, v in prose.items() if not k.startswith("_")}
        fm["status"] = "draft"
        chapters.write_prose(book, 1, fm, "返工后的新正文")
        assert client.get(f"/api/books/{book}/chapters/1/snapshots/1").json()["content"] == snap


# ---------------------------------------------------------------------------
# 5. PUT /locations —— 地点档案可编辑（规格04 §1）
# ---------------------------------------------------------------------------

class TestLocationsEdit:
    """原实现只有 GET，前端零入口，地点档案 UI 完全不可见不可编。"""

    def test_get_returns_empty_after_create(self, home):
        name = "地点书"
        books.create_book(name, genre_template="urban")
        assert client.get(f"/api/books/{name}/locations").json() == []

    def test_put_locations_roundtrip(self, home):
        name = "地点书"
        books.create_book(name, genre_template="urban")
        rows = [
            {"名称": "青云坊", "特征": "潮湿的坊市", "方位/距离": "城南三里",
             "首现章": "1", "关联剧情": "验令"},
            {"名称": "乱葬岗", "特征": "荒坟", "方位/距离": "东南三里",
             "首现章": "3", "关联剧情": "埋伏"},
        ]
        r = client.put(f"/api/books/{name}/locations", json={"rows": rows})
        assert r.status_code == 200 and r.json() == {"ok": True}

        got = client.get(f"/api/books/{name}/locations").json()
        assert got == rows
        # 落盘是 5 列固定表（地点档案无 frontmatter，是纯 Markdown 表）
        raw = (books.book_dir(name) / "LOCATIONS.md").read_text(encoding="utf-8")
        assert "| 名称 | 特征 | 方位/距离 | 首现章 | 关联剧情 |" in raw
        assert "| 青云坊 | 潮湿的坊市 | 城南三里 | 1 | 验令 |" in raw

    def test_put_locations_drops_blank_names(self, home):
        """空名称行无意义且会污染 FTS 关键词抽取 → 丢弃。"""
        name = "地点书"
        books.create_book(name, genre_template="urban")
        client.put(f"/api/books/{name}/locations", json={"rows": [
            {"名称": " "}, {"名称": "青云坊", "特征": "坊市"}]})
        got = client.get(f"/api/books/{name}/locations").json()
        assert [g["名称"] for g in got] == ["青云坊"]

    def test_put_locations_missing_columns_filled(self, home):
        """前端只传部分列时补空串，不报错（表格固定 5 列）。"""
        name = "地点书"
        books.create_book(name, genre_template="urban")
        client.put(f"/api/books/{name}/locations", json={"rows": [{"名称": "青云坊"}]})
        row = client.get(f"/api/books/{name}/locations").json()[0]
        assert row == {"名称": "青云坊", "特征": "", "方位/距离": "", "首现章": "",
                       "关联剧情": ""}

    def test_write_locations_function(self, book_env):
        """档案层函数本身可用（供内部调用/测试直连）。"""
        book, _ = book_env
        outline.write_locations(book, [{"名称": "土地庙", "特征": "塌了半边",
                                       "方位/距离": "岔路尽头", "首现章": "2",
                                       "关联剧情": "发现炭圈"}])
        rows = outline.read_locations(book)
        assert rows[0]["名称"] == "土地庙"
        assert rows[0]["关联剧情"] == "发现炭圈"


# ---------------------------------------------------------------------------
# 6. POST /export/md —— 导出 Markdown 合并版（规格04 §1）
# ---------------------------------------------------------------------------

class TestExportMarkdown:
    """原实现只有 export/txt 与 export/zip，规格04 §1 要求 txt/md 都能导。"""

    def _finalized_two(self, book: str) -> None:
        mock = MockLLM()
        for no in (1, 2):
            pipeline.generate_outline(book, no, client=mock)
            pipeline.confirm_outline(book, no)
            pipeline.generate_prose(book, no, client=mock)
            pipeline.run_mech_check(book, no, client=mock)
            pipeline.run_llm_review(book, no, client=mock)
            pipeline.finalize(book, no, client=mock)

    def test_md_export_structure(self, book_env):
        book, _ = book_env
        self._finalized_two(book)
        r = client.post(f"/api/books/{book}/export/md")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/markdown")
        ctype = r.headers["content-disposition"]
        assert ".md" in ctype and "attachment" in ctype

        text = r.text
        assert text.startswith(f"# {book}")
        assert "## 第1章" in text and "## 第2章" in text
        assert "林寒" in text                       # 正文原样带出
        assert "### 场景清单" not in text            # 细纲不该混进正文导出

    def test_md_export_respects_finalized_only(self, book_env):
        book, _ = book_env
        self._finalized_two(book)
        # 再造一个未定稿的第 3 章
        mock = MockLLM()
        pipeline.generate_outline(book, 3, client=mock)
        pipeline.confirm_outline(book, 3)
        pipeline.generate_prose(book, 3, client=mock)
        only_final = client.post(f"/api/books/{book}/export/md").text
        assert "## 第3章" not in only_final
        all_ch = client.post(f"/api/books/{book}/export/md?finalized_only=false").text
        assert "## 第3章" in all_ch

    def test_md_and_txt_share_body(self, book_env):
        """md 与 txt 正文完全一致，只是标题层级不同。"""
        book, _ = book_env
        self._finalized_two(book)
        md = client.post(f"/api/books/{book}/export/md").text
        txt = client.post(f"/api/books/{book}/export/txt").text
        body = chapters.read_prose(book, 1)["_body"].strip().splitlines()[0]
        assert body in md and body in txt
        assert "第1章" in txt and "## 第1章" in md


# ---------------------------------------------------------------------------
# 交叉：档案层新增函数不破坏既有 schema 校验 / 原子写
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fn_name", ["write_locations", "read_locations"])
def test_location_helpers_exist(home, fn_name):
    assert callable(getattr(outline, fn_name))


def test_location_columns_match_spec():
    """规格01 §2.6：地点档案固定 5 列。"""
    assert outline.LOC_COLS == ["名称", "特征", "方位/距离", "首现章", "关联剧情"]


def test_relationships_helper_not_removed():
    """characters.relationships() 原有读取能力必须保留（G4 写回依赖它去重）。"""
    assert callable(characters.relationships)
