"""（规格09 A-T01—03）本地候选资产经既有文风链路装配，不读外部原库。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from ainovel import assembler, books, prompts, style

ASSETS = Path(__file__).resolve().parents[1] / "style" / "候选范例"
PACK_IDS = ("restrained", "clear_action", "suspense", "urban", "historical", "light_daily")
TASKS = ("W1", "W3", "W6", "V1", "V2", "V3", "V5")


def _asset(pack_id: str) -> tuple[str, dict[str, Any]]:
    index = json.loads((ASSETS / "sources.json").read_text(encoding="utf-8"))
    pack = next(item for item in index["packages"] if item["id"] == pack_id)
    return (ASSETS / f"{pack_id}.txt").read_text(encoding="utf-8"), pack


def _install(book: str, pack_id: str = "restrained") -> tuple[str, dict[str, Any]]:
    corpus, pack = _asset(pack_id)
    fp = style.write_corpus(book, corpus)
    style.save_examples(book, fp, [{key: item[key] for key in ("start_paragraph", "end_paragraph", "scene_types")}
                                  for item in pack["examples"]])
    style.write_card(book, "测试书人工卡标记：表达服从本章场景，不复制参考设定。")
    style.set_enabled(book, True)
    return corpus, pack


def _variables(book: str, task: str) -> dict[str, Any]:
    values = {key: f"临时素材{key}" for key in re.findall(r"〖([^〗]+)〗", prompts.get(task, book).body)}
    values.update(装配上下文="【出场角色状态卡】\n不可裁剪角色约束\n\n【本章细纲合同】\n唯一合同",
                  章合同="唯一合同")
    return values


@pytest.mark.parametrize("pack_id", PACK_IDS)
def test_reference_assets_match_index(home: Path, pack_id: str) -> None:
    corpus, pack = _asset(pack_id)
    assert pack["status"] == "candidate" and pack["approved_by_user"] is False
    assert style.fingerprint(corpus) == pack["corpus_fingerprint"]
    raw = (ASSETS / f"{pack_id}.txt").read_bytes()
    assert b"\r" not in raw and not raw.startswith(b"\xef\xbb\xbf")
    paragraphs = style._paragraphs(corpus)
    assert len(pack["examples"]) == 2
    chars = 0
    previous_end = 0
    for item in pack["examples"]:
        assert item["start_paragraph"] == previous_end + 1
        previous_end = item["end_paragraph"]
        text = style._range_text(corpus, paragraphs, item["start_paragraph"], item["end_paragraph"])
        assert style.fingerprint(text) == item["content_fingerprint"]
        assert len(re.sub(r"\s+", "", text)) == item["visible_chars"]
        assert len(re.findall(r"[0-9a-f]", item["source_text_sha256"])) == 64
        chars += item["visible_chars"]
    assert previous_end == len(paragraphs) and chars <= 1200


@pytest.mark.parametrize("pack_id", PACK_IDS)
def test_reference_examples_reach_requests(book_env: Any, pack_id: str) -> None:
    book, _ = book_env
    corpus, pack = _install(book, pack_id)
    expected = {item["content_fingerprint"] for item in pack["examples"]}
    for task in TASKS:
        request = assembler.render_request(book, task, _variables(book, task))
        assert request == assembler.render_request(book, task, _variables(book, task))
        selected = style.context(book, task)["examples"]
        assert len(selected) == 2
        assert {item["content_fingerprint"] for item in selected} == expected
        assert request["meta"]["style"]["corpus_fingerprint"] == style.fingerprint(corpus)
        assert {item["id"] for item in request["meta"]["style"]["examples"]} == {item["id"] for item in selected}
        for item in selected:
            assert not item["legacy"]
            assert request["user"].count(item["text"]) == 1
            assert item["text"] not in request["system"]
        assert "不得复制角色、情节或专有设定" in request["user"]
        for other_id in set(PACK_IDS) - {pack_id}:
            other, _ = _asset(other_id)
            assert style._paragraphs(other)[0]["text"] not in request["user"]
    assert style.read_corpus(book) == corpus


@pytest.mark.parametrize("task", TASKS)
def test_reference_assets_off_does_not_inject(book_env: Any, task: str) -> None:
    book, _ = book_env
    _install(book)
    text = style.context(book, task)["examples"][0]["text"]
    style.set_enabled(book, False)
    request = assembler.render_request(book, task, _variables(book, task))
    assert request["meta"]["style"]["examples"] == []
    assert text not in request["user"] and "测试书人工卡标记" not in request["user"]
    style.write_card(book, "")
    style.set_enabled(book, True)
    assert not style.enabled(book)
    request = assembler.render_request(book, task, _variables(book, task))
    assert request["meta"]["style"]["examples"] == []
    assert text not in request["user"]


def test_reference_corpus_change_is_stale(book_env: Any) -> None:
    book, _ = book_env
    corpus, _ = _install(book)
    original_index = style.examples_path(book).read_bytes()
    style.write_corpus(book, corpus + "\n新段落。")
    assert style.describe(book)["examples_stale"]
    assert style.context(book)["examples"] == []
    assert style.examples_path(book).read_bytes() == original_index
    request = assembler.render_request(book, "W1", _variables(book, "W1"))
    assert request["meta"]["style"]["examples"] == []
    assert any("过期" in item for item in request["meta"]["warnings"])


def test_reference_budget_drops_complete_examples(book_env: Any) -> None:
    book, _ = book_env
    _install(book)
    values = _variables(book, "W1")
    full = assembler.render_request(book, "W1", values)
    config = books.load_config(book)
    config["assembly_budget_overrides"] = {"total": full["meta"]["est_tokens"]["total"] - 1}
    books.save_config(book, config)
    cut = assembler.render_request(book, "W1", values)
    ids = {item["id"] for item in cut["meta"]["style"]["examples"]}
    assert len(ids) == 1 and cut["meta"]["truncated"][0]["block"] == "style_example"
    for item in style.context(book)["examples"]:
        assert (item["text"] in cut["user"]) == (item["id"] in ids)
    assert "不可裁剪角色约束" in cut["user"] and "唯一合同" in cut["user"]
    config["assembly_budget_overrides"] = {"total": 1}
    books.save_config(book, config)
    with pytest.raises(prompts.PromptError, match="PROMPT_BUDGET_EXCEEDED"):
        assembler.render_request(book, "W1", values)
