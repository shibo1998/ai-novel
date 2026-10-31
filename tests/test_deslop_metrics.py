"""（规格09 B-T01—03）透明计量可重复，旧检查和模型摘要不受影响。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from ainovel import deslop, pipeline, stylemetrics


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_paragraph_metrics_are_stable(home: Path, newline: str) -> None:
    text = newline.join(["", "甲乙", "丙丁", " \t", "字" * 10, "", "字" * 20, "", "字" * 30, ""])
    metrics = stylemetrics.analyze(text)
    assert metrics["version"] == 1
    assert metrics["paragraphs"] == {"count": 4, "mean_chars": 16.0, "median_chars": 15.0,
                                     "p90_chars": 30, "short_ratio": 0.5}
    assert metrics == stylemetrics.analyze(text)
    json.dumps(metrics, allow_nan=False)


def test_p90_uses_nearest_rank(home: Path) -> None:
    paragraphs = ["字" * size for size in range(1, 11)]
    result = stylemetrics.analyze("\n\n".join(paragraphs))["paragraphs"]
    assert result == {"count": 10, "mean_chars": 5.5, "median_chars": 5.5,
                      "p90_chars": 9, "short_ratio": 1.0}
    assert stylemetrics.analyze("甲\n乙\n丙")["paragraphs"]["count"] == 1
    assert stylemetrics.analyze("甲\n　\n乙")["paragraphs"]["count"] == 2


@pytest.mark.parametrize("body", ["", " \t\r\n\r\n　"])
def test_empty_metrics_do_not_claim_density(home: Path, body: str) -> None:
    metrics = deslop.analyze(body).to_dict()["metrics"]
    assert metrics["paragraphs"] == {"count": 0, "mean_chars": None, "median_chars": None,
                                     "p90_chars": None, "short_ratio": None}
    assert metrics["simile_cues"]["count"] == 0
    assert metrics["simile_cues"]["per_1000_chars"] is None
    assert deslop.analyze(body).score == -1


def test_simile_cues_are_explicit_and_nonoverlapping(home: Path) -> None:
    (home / "lint" / "simile_cues.txt").write_text("像\n好像\n仿佛\n如同\n", encoding="utf-8", newline="")
    body = "头像图像像素画像摄像机录像没有比喻。\r\n\r\n“好像一扇门。”\r\n仿佛有光，如同薄纸。"
    cues = stylemetrics.analyze(body)["simile_cues"]
    assert cues["status"] == "measured" and cues["scope"] == "all_text"
    assert cues["count"] == 3
    assert cues["per_1000_chars"] == round(3000 / len("".join(body.split())), 2)
    assert [item["cue"] for item in cues["positions"]] == ["好像", "仿佛", "如同"]
    assert [item["line"] for item in cues["positions"]] == [3, 4, 4]
    for item in cues["positions"]:
        assert body[item["start"]:item["end"]] == item["cue"]


def test_positions_are_bounded_but_count_is_complete(home: Path) -> None:
    body = "如同薄纸。\n\n" * 27
    cues = stylemetrics.analyze(body)["simile_cues"]
    assert cues["count"] == 27 and len(cues["positions"]) == 20
    assert cues["positions"][-1]["line"] == 39


@pytest.mark.parametrize("filename", ["simile_cues.txt", "simile_exclusions.txt"])
@pytest.mark.parametrize("bad", [None, "", "# 只有注释\n", "像 空\n", "像\x00\n", "字" * 65])
def test_metrics_unavailable_is_not_zero(home: Path, filename: str, bad: str | None) -> None:
    path = home / "lint" / filename
    if bad is None:
        path.unlink()
    else:
        path.write_text(bad, encoding="utf-8", newline="")
    body = "如同薄纸。" * 25
    result = deslop.analyze(body)
    cues = result.to_dict()["metrics"]["simile_cues"]
    assert cues["status"] == "unavailable" and cues["reason"]
    assert cues["count"] is None and cues["per_1000_chars"] is None
    assert result.to_dict()["metrics"]["paragraphs"]["count"] == 1
    assert result.blocking == []
    assert not any("simile" in str(item) for item in result.ineffective)


def test_invalid_encoding_is_unavailable(home: Path) -> None:
    (home / "lint" / "simile_cues.txt").write_bytes(b"\xff\xff")
    result = stylemetrics.analyze("如同薄纸。")
    assert result["simile_cues"]["status"] == "unavailable"


@pytest.mark.parametrize("body", ["风像冰冷的铁片掠过他的面颊。\n\n" * 10, "不是退缩，而是寻找出口。——门缝有光。" * 10, "像刀。"])
def test_metrics_do_not_change_deslop_verdict(home: Path, monkeypatch: Any, body: str) -> None:
    normal = deslop.analyze(body)
    brief = pipeline._deslop_brief(body)
    before = normal.to_dict()
    assert before["metrics"] == stylemetrics.analyze(body)
    monkeypatch.setattr(stylemetrics, "analyze", lambda *_: {"version": 1, "diagnostic_only": True})
    changed = deslop.analyze(body)
    after = changed.to_dict()
    before.pop("metrics")
    after.pop("metrics")
    assert before == after
    assert normal.raw_penalty == changed.raw_penalty
    assert normal.top_hits() == changed.top_hits()
    assert normal.blocking == changed.blocking
    assert pipeline._deslop_brief(body) == brief


def test_simile_metrics_are_independent_of_d14_preserve(home: Path) -> None:
    body = "风像冰冷的铁片掠过他的面颊。\n\n" * 10
    result = deslop.analyze(body)
    assert not any(hit.rule == "D-14" for hit in result.hits)
    assert result.to_dict()["metrics"]["simile_cues"]["count"] == 10


def test_deslop_endpoint_returns_metrics_without_writing(book_env: Any) -> None:
    from fastapi.testclient import TestClient
    from ainovel import api, chapters

    book, home = book_env
    body = "风像薄纸。\n\n他停下。"
    chapters.write_prose(book, 1, {"chapter_no": 1, "version": 1, "status": "draft",
                                 "word_count": chapters.count_words(body), "finalized_at": None}, body)
    paths = [path for path in (home / "books" / book).rglob("*") if path.is_file()]
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    response = TestClient(api.app).get(f"/api/books/{book}/chapters/1/deslop")
    assert response.status_code == 200
    assert response.json()["metrics"]["paragraphs"]["count"] == 2
    assert response.json()["metrics"]["simile_cues"]["count"] == 1
    assert {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths} == before


def test_overlapping_exclusions_are_unioned(home: Path) -> None:
    (home / "lint" / "simile_exclusions.txt").write_text("甲乙\n乙像\n", encoding="utf-8", newline="")
    result = stylemetrics.analyze("甲乙像")["simile_cues"]
    assert result["status"] == "measured"
    assert result["count"] == 0 and result["positions"] == []
