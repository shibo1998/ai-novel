"""（规格06 AC01-09/23/24）提示装配与文风资产离线回归。"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any

import pytest

from ainovel import assembler, atomic, books, db, deslop, mechcheck, prompts, settings, style


BASE_VERSIONS = {"S0": 2, "S1": 4, "S2": 2, "F1": 2, "F2": 2, "G6": 3,
                 "W1": 5, "W2": 3, "W3": 2, "W4": 3, "W5": 3, "W6": 2,
                 "V1": 2, "V2": 3, "V3": 2, "V5": 2}


def _variables(pid: str, book: str) -> dict[str, Any]:
    p = prompts.get(pid, book)
    values = {name: f"素材{name}" for name in re.findall(r"〖([^〗]+)〗", p.body)}
    values.update({"系统段": "旧内嵌系统不可发送", "章合同": "唯一合同标记", "装配上下文": "【出场角色状态卡】\n角色硬约束标记\n\n【本章细纲合同】\n唯一合同标记",
                   "范文样本": "旧样例不得重复", "文风要点": "旧卡要点不得注入"})
    return values


def _assets(book: str) -> tuple[str, str]:
    fp = style.write_corpus(book, "风格范例第一段。\n换行仍属同段。\n\n风格范例第二段。\n\n对白范例：你先把门关上。")
    card = style.write_card(book, "## 句法特征\n正式卡片唯一标记，保留功能性长句。")
    style.set_enabled(book, True)
    return fp, card


def test_all_ids_versions_and_body_only(home: Any) -> None:
    lib = prompts.library()
    assert set(lib) == {"S0", "S1", "S2", "F1", "F2", "T1", "G1", "G2", "G3", "G4", "G5", "G6",
                        "W1", "W2", "W3", "W4", "W5", "W6", "V1", "V2", "V3", "V4", "V5", "V6"}
    for pid, prompt in lib.items():
        args = {k: "测试素材" for k in re.findall(r"〖([^〗]+)〗", prompt.body)}
        _, rendered = prompts.render(pid, args)
        assert "〖" not in rendered
        assert not rendered.startswith("---\n")
        assert "model_stage:" not in rendered
        assert prompt.digest == hashlib.sha256(prompt.path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        assert prompt.full_text().startswith("---\n")
        if pid in BASE_VERSIONS:
            assert prompt.fm["version"] > BASE_VERSIONS[pid]


def test_w1_unique_contract_and_real_roles(book_env: Any) -> None:
    book, _ = book_env
    values = _variables("W1", book)
    req = assembler.render_request(book, "W1", values)
    assert req["user"].count("唯一合同标记") == 1
    assert req["user"].count("角色硬约束标记") == 1
    assert "旧内嵌系统不可发送" not in req["user"]
    assert "约束层次" in req["system"] and "约束层次" not in req["user"]
    assert "model_stage:" not in req["system"] + req["user"]
    assert req["meta"]["templates"][0]["digest"] == prompts.get("W1", book).digest
    assert req == assembler.render_request(book, "W1", values)
    assert values["章合同"] == "唯一合同标记"


@pytest.mark.parametrize("pid", sorted(assembler.STYLE_TASKS))
def test_whole_chain_style_toggle(book_env: Any, pid: str, monkeypatch: Any) -> None:
    book, _ = book_env
    _assets(book)
    values = _variables(pid, book)
    on = assembler.render_request(book, pid, values)
    assert on["user"].count("正式卡片唯一标记") == 1
    assert on["user"].count("风格范例第一段。\n换行仍属同段。") == 1
    assert "旧样例不得重复" not in on["user"]
    assert "旧卡要点不得注入" not in on["user"]
    assert "正式卡片唯一标记" not in on["system"]
    style.set_enabled(book, False)
    monkeypatch.setattr(style, "read_card", lambda *_: pytest.fail("关闭时读取了卡片"))
    monkeypatch.setattr(style, "read_corpus", lambda *_: pytest.fail("关闭时读取了语料"))
    off = assembler.render_request(book, pid, values)
    assert not off["meta"]["style"]["enabled"]
    assert off["meta"]["style"]["examples"] == []
    assert "正式卡片唯一标记" not in off["system"] + off["user"]
    assert "风格范例第一段" not in off["system"] + off["user"]


def test_index_stable_selection_and_stale(book_env: Any) -> None:
    book, _ = book_env
    fp, _ = _assets(book)
    examples = [{"start_paragraph": 1, "end_paragraph": 2, "scene_types": ["general"]},
                {"start_paragraph": 3, "end_paragraph": 3, "scene_types": ["dialogue"]}]
    index = style.save_examples(book, fp, examples)
    assert index == style.save_examples(book, fp, list(reversed(examples)))
    selected = style.context(book, "W2")
    assert selected == style.context(book, "W2")
    assert selected["examples"][0]["text"] == "对白范例：你先把门关上。"
    assert all(not e["legacy"] for e in selected["examples"])
    assert selected["examples"][1]["text"] == "风格范例第一段。\n换行仍属同段。\n\n风格范例第二段。"
    old = style.examples_path(book).read_bytes()
    style.write_corpus(book, "新语料。", fp)
    assert style.describe(book)["examples_stale"] is True
    assert style.context(book)["examples"] == []
    assert old == style.examples_path(book).read_bytes()
    with pytest.raises(style.StyleConflict):
        style.save_examples(book, fp, examples)


def test_crlf_paragraphs_are_complete(book_env: Any) -> None:
    book, _ = book_env
    fp = style.write_corpus(book, "首行。\r\n同段。\r\n\r\n第二段。\r\n")
    desc = style.describe(book)
    assert desc["corpus_fingerprint"] == fp
    assert desc["paragraphs"] == [{"id": "p0001", "no": 1, "text": "首行。\r\n同段。"},
                                  {"id": "p0002", "no": 2, "text": "第二段。"}]
    index = style.save_examples(book, fp, [{"start_paragraph": 1, "end_paragraph": 2, "scene_types": ["general"]}])
    assert index["examples"][0]["content_fingerprint"] == style.fingerprint("首行。\r\n同段。\r\n\r\n第二段。")


@pytest.mark.parametrize("item", [
    {"start_paragraph": 0, "end_paragraph": 1}, {"start_paragraph": 1, "end_paragraph": 4},
    {"start_paragraph": 2, "end_paragraph": 1}, {"start_paragraph": True, "end_paragraph": 1},
    {"start_paragraph": 1, "end_paragraph": 1, "scene_types": ["bad"]},
])
def test_invalid_examples_never_write(book_env: Any, item: dict[str, Any]) -> None:
    book, _ = book_env
    fp, _ = _assets(book)
    with pytest.raises(ValueError):
        style.save_examples(book, fp, [item])
    assert not style.examples_path(book).exists()


def test_legacy_complete_examples_and_oversize(book_env: Any) -> None:
    book, _ = book_env
    style.write_corpus(book, "长" * 1201 + "。\n\n完整短段。")
    style.write_card(book, "正式卡")
    style.set_enabled(book, True)
    ctx = style.context(book, "W3")
    assert [e["text"] for e in ctx["examples"]] == ["完整短段。"]
    assert ctx["examples"][0]["legacy"]
    assert any("整段舍弃" in w for w in ctx["warnings"])
    assert not style.examples_path(book).exists()


def test_card_corpus_optimistic_conflicts(book_env: Any) -> None:
    book, _ = book_env
    empty = style.fingerprint("")
    new = style.write_card(book, "人工正式卡", empty)
    assert style.describe(book)["card_fingerprint"] == new
    with pytest.raises(style.StyleConflict):
        style.write_card(book, "旧页面", empty)
    assert style.read_card(book) == "人工正式卡"
    fp = style.write_corpus(book, "第一版", empty)
    with pytest.raises(style.StyleConflict):
        style.write_corpus(book, "第二版", empty)
    assert style.describe(book)["corpus_fingerprint"] == fp


def test_genre_branch_default_and_s1_whitelist(book_env: Any) -> None:
    book, _ = book_env
    cfg = books.load_config(book)
    cfg["genre_template"] = "xianxia"
    cfg["lint_whitelist"] = [mechcheck.load_banned_graded()["L1"][0]]
    books.save_config(book, cfg)
    req = assembler.render_request(book, "V5", _variables("V5", book))
    assert "S2" not in [t["id"] for t in req["meta"]["templates"]]
    assert "都市题材适配" not in req["system"]
    args = assembler._s1_vars(book)
    assert cfg["lint_whitelist"][0] not in args["禁词表"]
    assert cfg["lint_whitelist"][0] in args["禁词白名单"]
    assert "L2语境敏感词" in req["system"]
    del cfg["genre_template"]
    books.save_config(book, cfg)
    default = assembler.render_request(book, "V5", _variables("V5", book))
    assert default["meta"]["genre_source"] == "default:urban"
    assert "S2" in [t["id"] for t in default["meta"]["templates"]]


def test_budget_all_input_and_cut_priority(book_env: Any) -> None:
    book, _ = book_env
    _assets(book)
    values = _variables("W1", book)
    optional = {"fts_recall": "检索" * 1000, "recaps": "摘要" * 1000,
                "bible_core": "条目" * 1000 + "\n\n【时间线约束】\n不可截断知情禁区"}
    values["装配上下文"] = assembler.Assembly(slots={**optional, "char_cards": "不可截断角色", "outline_contract": "唯一合同标记"}).context_text()
    full = assembler.render_request(book, "W1", values, "重试要求")
    estimates = full["meta"]["est_tokens"]
    assert estimates["total"] == assembler.est_tokens(full["system"]) + assembler.est_tokens(full["user"])
    settings.update({"assembly_budget_tokens": estimates["total"] - 3500})
    cut = assembler.render_request(book, "W1", values, "重试要求")
    assert [r["block"] for r in cut["meta"]["truncated"]] == ["fts_recall", "recaps", "bible_core"]
    assert "不可截断角色" in cut["user"] and "唯一合同标记" in cut["user"]
    assert "不可截断知情禁区" in cut["user"] and "重试要求" in cut["user"]
    assert cut["meta"]["style"]["examples"]
    assert cut["meta"]["est_tokens"]["total"] <= cut["meta"]["est_tokens"]["budget"]
    settings.update({"assembly_budget_tokens": 5})
    with pytest.raises(prompts.PromptError, match="PROMPT_BUDGET_EXCEEDED"):
        assembler.render_request(book, "W1", values)


def test_budget_drops_whole_examples_before_rejecting_hard_input(book_env: Any) -> None:
    book, _ = book_env
    _assets(book)
    values = _variables("V3", book)
    full = assembler.render_request(book, "V3", values)
    settings.update({"assembly_budget_tokens": full["meta"]["est_tokens"]["total"] - 1})
    cut = assembler.render_request(book, "V3", values)
    assert cut["meta"]["truncated"][0]["block"] == "style_example"
    assert values["原稿全文"] in cut["user"] and values["润色后全文"] in cut["user"]
    assert "风格范例第二段。" not in cut["user"]
    settings.update({"assembly_budget_tokens": full["meta"]["est_tokens"]["total"] + 10})
    with pytest.raises(prompts.PromptError, match="PROMPT_BUDGET_EXCEEDED"):
        assembler.render_request(book, "V3", values, "重试" * 10000)


def test_legacy_override_system_adapter_and_interface_gate(book_env: Any) -> None:
    book, home = book_env
    override = home / "books" / book / "prompts" / "review" / "V5.md"
    original = prompts.get("V5", book).full_text()
    atomic.write_text(override, original + "\n〖系统段〗\n我的书级要求。")
    req = assembler.render_request(book, "V5", _variables("V5", book))
    assert "我的书级要求。" in req["user"] and "旧内嵌系统不可发送" not in req["user"]
    assert req["meta"]["templates"][0]["source"] == "book"
    assert any("legacy" in w for w in req["meta"]["warnings"])
    v2 = prompts.get("V2", book)
    atomic.write_text(override.with_name("V2.md"), v2.full_text().replace("interface_version: 2", "interface_version: 1"))
    with pytest.raises(prompts.PromptError, match="PROMPT_UPGRADE_REQUIRED: V2"):
        assembler.render_request(book, "V2", {})


def test_v2_v3_fixed_interfaces_and_g6_recent_input(home: Any) -> None:
    assert prompts.get("V2").fm["inputs"] == ["原稿全文", "原稿分块JSON", "选区", "章合同", "角色约束", "知情边界", "检测器命中"]
    assert prompts.get("V3").fm["inputs"] == ["原稿全文", "润色后全文", "改动清单", "章合同", "角色约束", "知情边界"]
    for pid in ["V2", "V3"]:
        assert prompts.get(pid).fm["interface_version"] == 2
    assert "最近三章节奏" in prompts.get("G6").fm["inputs"]
    assert "issues允许为空" in prompts.get("V1").body


def test_v1_empty_issues_still_validates_scores(book_env: Any) -> None:
    from ainovel import llm, pipeline

    good = {"scores": {k: 90 for k in pipeline.RUBRIC_DIMS}, "issues": [], "verdict": "pass"}
    assert pipeline._validate_review(good)["issues"] == []
    with pytest.raises(llm.LLMError):
        pipeline._validate_review({**good, "scores": {"plot": 90}})


def test_advisory_m12_keeps_historical_levels_and_hard_gates(home: Any) -> None:
    body = "抬手——落刀。" * 300
    result = mechcheck.check_full(body, word_range=[100, 10000])
    hit = next(h for h in result.hits if h.rule == "M-12")
    assert hit.level == "block" and hit.effective_level == "warn"
    assert hit.to_dict()["effective_level"] == "warn"
    assert result.verdict == "pass" and not result.blocks
    assert not mechcheck.has_block([hit])
    detected = deslop.analyze(body)
    assert detected.blocking == []
    assert any(h.level == "blocking" for h in detected.hits)
    assert all(h.to_dict()["effective_level"] == "advisory" for h in detected.hits)
    hard = mechcheck.check_full(body + "国家领导人", word_range=[100, 10000], whitelist=["国家领导人"])
    assert hard.verdict == "blocked" and mechcheck.has_sensitive(hard.hits)
    (home / "lint" / "sensitive_words.txt").unlink()
    assert mechcheck.check_full(body, word_range=[100, 10000]).verdict == "not_effective"


def test_db_nullable_migration_and_request_evidence(book_env: Any) -> None:
    book, _ = book_env
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE generation_log(id INTEGER PRIMARY KEY, legacy TEXT)")
    conn.execute("INSERT INTO generation_log(legacy) VALUES ('历史')")
    db.init_schema(conn)
    assert conn.execute("SELECT request_id, request_meta_json FROM generation_log").fetchone() == (None, None)
    conn.close()
    req = assembler.render_request(book, "V5", _variables("V5", book))
    log_id = db.log_generation(book_id=books.book_id(book), chapter_no=1, stage="review", request_id="test-request",
                               request_meta=req["meta"], prompt_digest=prompts.get("V5", book).digest,
                               input_tokens=10, output_tokens=20, cost_millicents=30)
    row = db.get_conn().execute("SELECT * FROM generation_log WHERE id=?", (log_id,)).fetchone()
    assert row["request_id"] == "test-request"
    assert json.loads(row["request_meta_json"])["request_digest"] == req["meta"]["request_digest"]
    assert row["prompt_digest"] == prompts.get("V5", book).digest
    assert row["input_tokens"] == 10 and row["cost_millicents"] == 30


def test_log_write_failure_propagates(book_env: Any, monkeypatch: Any) -> None:
    from contextlib import contextmanager

    book, _ = book_env
    bid = books.book_id(book)

    @contextmanager
    def broken_tx() -> Any:
        raise sqlite3.OperationalError("日志写入失败")
        yield

    monkeypatch.setattr(db, "tx", broken_tx)
    with pytest.raises(sqlite3.OperationalError, match="日志写入失败"):
        db.log_generation(book_id=bid, chapter_no=1, stage="review", request_meta={"request_digest": "x"})


def test_f1_only_returns_draft_and_logs_request(book_env: Any) -> None:
    from ainovel import pipeline
    from ainovel.mockllm import MockLLM

    book, _ = book_env
    fp, card_fp = _assets(book)
    before = style.read_card(book)
    result, template, _ = pipeline._call(book, None, "style", "F1", {"语料样本": style.read_corpus(book)}, client=MockLLM())
    assert "句法特征" in result.text and template.id == "F1"
    assert style.read_card(book) == before
    assert style.describe(book)["card_fingerprint"] == card_fp
    assert style.describe(book)["corpus_fingerprint"] == fp
    row = db.get_conn().execute("SELECT * FROM generation_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["request_id"] and json.loads(row["request_meta_json"])["request_digest"]
    assert row["input_tokens"] > 0 and row["output_tokens"] > 0


def test_config_switch_without_card_is_visible(book_env: Any) -> None:
    book, _ = book_env
    style.set_enabled(book, True)
    assert books.load_config(book)["style_enabled"] is True
    assert style.enabled(book) is False
    ctx = style.context(book)
    assert ctx["enabled"] is False and ctx["card"] == "" and ctx["examples"] == []


def test_actual_context_preserves_prepreview_slots_and_raw_mapping(book_env: Any) -> None:
    from ainovel import pipeline
    from ainovel.mockllm import MockLLM

    book, _ = book_env
    pipeline.generate_outline(book, 1, client=MockLLM())
    settings.update({"assembly_budget_tokens": 50})
    assembled = assembler.assemble(book, 1)
    assert assembled.truncated
    assert assembled.original_slots and assembled.original_slots["bible_core"] in assembled.context_text()
    settings.update({"assembly_budget_tokens": 32000})
    values = _variables("W1", book)
    values["装配上下文"] = assembled
    request = assembler.render_request(book, "W1", values)
    assert assembled.original_slots["char_cards"] in request["user"]
    values["装配上下文"] = {"slots": assembled.original_slots}
    mapped = assembler.render_request(book, "W1", values)
    assert request["user"] == mapped["user"]
