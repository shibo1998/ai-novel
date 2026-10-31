"""（规格11 §6）同题材故事候选；只用home/MockLLM，绝不触碰真实书。"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from ainovel import atomic, books, db, outline, prompts, storyideas
from ainovel.mockllm import MockLLM


def _book(home: Path, genre: str = "urban") -> str:
    name = "灵感测试书"
    books.create_book(name, genre_template=genre, target_words=30000)
    return name


def _files(book: str) -> dict[str, str]:
    root = books.book_dir(book)
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


def _generate(book: str, client: MockLLM, preferences: str = "", previous: list[str] | None = None) -> dict[str, Any]:
    return storyideas.generate(book, preferences, previous or [], storyideas.context(book)["etag"], client=client)


@pytest.mark.parametrize("genre", list(books.GENRE_TEMPLATES))
def test_empty_preferences_three_ideas_source_genre_and_no_archive_write(home: Path, genre: str) -> None:
    book = _book(home, genre)
    before = _files(book)
    from ainovel import fts
    with db.tx() as conn:
        fts.ensure_table(conn)
        indexed_before = list(conn.execute("SELECT book_id, chapter_no, body FROM chapters_fts"))
        state_before = {table: list(conn.execute(f"SELECT * FROM {table}")) for table in ("chapters", "chapter_state", "snapshots", "review_records")}
    client = MockLLM()
    ctx = storyideas.context(book)
    assert ctx["eligible"] and ctx["genre_template"] == genre
    result = _generate(book, client)
    assert result["genre_template"] == genre and result["genre"] == books.GENRE_TEMPLATES[genre]
    assert len(result["ideas"]) == 3 and len({idea["logline"] for idea in result["ideas"]}) == 3
    assert len(client.calls) == 1 and "结构化故事候选" in client.calls[0]["user"]
    assert genre in client.calls[0]["user"]
    assert _files(book) == before
    with db.tx() as conn:
        rows = conn.execute("SELECT * FROM generation_log WHERE book_id=?", (books.book_id(book),)).fetchall()
        assert len(rows) == 1 and rows[0]["prompt_id"] == "T1" and rows[0]["status"] == "ok"
        assert rows[0]["request_id"] == result["request_id"]
        assert conn.execute("SELECT count(*) FROM chapters").fetchone()[0] == 0
        assert list(conn.execute("SELECT book_id, chapter_no, body FROM chapters_fts")) == indexed_before
        assert {table: list(conn.execute(f"SELECT * FROM {table}")) for table in state_before} == state_before


def test_preferences_and_next_batch_are_explicit_single_calls(home: Path) -> None:
    book = _book(home)
    client = MockLLM()
    first = _generate(book, client, "不要系统流，偏轻松")
    previous = [idea["logline"] for idea in first["ideas"]]
    second = _generate(book, client, "不要系统流，偏轻松", previous)
    assert len(client.calls) == 2
    assert "不要系统流" in client.calls[-1]["user"] and previous[0] in client.calls[-1]["user"]
    assert not set(previous) & {idea["logline"] for idea in second["ideas"]}


@pytest.mark.parametrize("state", ["volume", "row", "chapter_file"])
def test_published_story_is_not_eligible(home: Path, state: str) -> None:
    book = _book(home)
    if state == "volume":
        outline.write_outline(book, {"logline": "", "volumes": [{"no": 1, "chapters": [1, 3]}]}, [])
    elif state == "row":
        outline.write_outline(book, {"logline": "", "volumes": []}, [{"章": "1", "卷": "1", "本章目标": "已有规划"}])
    else:
        atomic.write_text(books.book_dir(book) / "chapters" / "001_正文.draft.md", "已落盘稿")
    ctx = storyideas.context(book)
    assert not ctx["eligible"]
    client = MockLLM()
    with pytest.raises(storyideas.StoryIdeasError) as error:
        storyideas.generate(book, "", [], ctx["etag"], client=client)
    assert error.value.status == 409 and client.calls == []


@pytest.mark.parametrize("cfg", [None, "bad-json", {}, {"genre_template": "other"}, {"genre_template": []}])
def test_bad_config_cannot_fallback_to_urban(home: Path, cfg: Any) -> None:
    book = _book(home)
    path = books.book_dir(book) / "config.json"
    if cfg is None:
        path.unlink()
    else:
        atomic.write_text(path, cfg if isinstance(cfg, str) else json.dumps(cfg))
    with pytest.raises(storyideas.StoryIdeasError) as error:
        storyideas.context(book)
    assert error.value.status == 503


@pytest.mark.parametrize("payload", ["bad", "---\nlogline: ''\nvolumes: []\n---\n随意无章节总表", "---\nlogline: ''\nvolumes: wrong\n---\n"])
def test_bad_outline_is_not_empty_story(home: Path, payload: str) -> None:
    book = _book(home)
    atomic.write_text(books.book_dir(book) / "OUTLINE.md", payload)
    with pytest.raises(storyideas.StoryIdeasError) as error:
        storyideas.context(book)
    assert error.value.status == 503

def test_outline_header_without_separator_is_unavailable(home: Path) -> None:
    book = _book(home)
    path = books.book_dir(book) / "OUTLINE.md"
    lines = path.read_text(encoding="utf-8").splitlines()
    marker = next(i for i, line in enumerate(lines) if line.strip().startswith("|") and "本章目标" in line)
    lines[marker + 1] = "| 1 | 1 | 已有章节 | 阻力 | 信息 | 悬念 | 3 |"
    atomic.write_text(path, "\n".join(lines) + "\n")
    client = MockLLM()
    with pytest.raises(storyideas.StoryIdeasError) as error:
        storyideas.generate(book, "", [], '"previous"', client=client)
    assert error.value.status == 503 and client.calls == []

def test_outline_table_header_without_separator_is_unavailable(home: Path) -> None:
    book = _book(home)
    outline_path = books.book_dir(book) / "OUTLINE.md"
    text = outline_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    marker = next(i for i, line in enumerate(lines) if line.startswith("| 章 |") or (line.startswith("|") and "本章目标" in line))
    lines[marker + 1] = "| 缺分隔符 |"
    atomic.write_text(outline_path, "\n".join(lines) + "\n")
    with pytest.raises(storyideas.StoryIdeasError) as error:
        storyideas.context(book)
    assert error.value.status == 503


def test_context_does_not_rewrite_interrupted_task(home: Path) -> None:
    from ainovel import production, volumeproduction
    book = _book(home)
    original = volumeproduction.prepare
    volumeproduction.prepare = lambda *_args, **_kwargs: {}  # 只留任务，最终恢复原函数
    try:
        job = production.create(book, {"mode": "prepare", "count": 3, "logline": "测试", "client_request_id": str(uuid4())}, production.book_etag(book), background=False)
    finally:
        volumeproduction.prepare = original
    path = production.directory(book, job["id"]) / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8")); manifest["status"] = "preparing"
    atomic.write_text(path, json.dumps(manifest, ensure_ascii=False))
    before = path.read_bytes()
    assert not storyideas.context(book)["eligible"]
    assert path.read_bytes() == before
    atomic.write_text(path, "{}")
    with pytest.raises(storyideas.StoryIdeasError) as error:
        storyideas.context(book)
    assert error.value.status == 503


def test_etag_required_and_changed_inputs_reject_before_call(home: Path) -> None:
    book = _book(home)
    ctx = storyideas.context(book)
    client = MockLLM()
    with pytest.raises(storyideas.StoryIdeasError) as error:
        storyideas.generate(book, "", [], None, client=client)
    assert error.value.status == 428
    path = books.book_dir(book) / "meta.md"
    atomic.write_text(path, path.read_text(encoding="utf-8") + "\n人工编辑。")
    with pytest.raises(storyideas.StoryIdeasError) as error:
        storyideas.generate(book, "", [], ctx["etag"], client=client)
    assert error.value.status == 412 and client.calls == []


def test_third_party_change_during_response_invalidates_ideas(home: Path) -> None:
    book = _book(home)
    class ChangedMock(MockLLM):
        def chat(self, **kwargs: Any) -> Any:
            res = super().chat(**kwargs)
            path = books.book_dir(book) / "meta.md"
            atomic.write_text(path, path.read_text(encoding="utf-8") + "\n外部改动")
            return res
    client = ChangedMock()
    with pytest.raises(storyideas.StoryIdeasError) as error:
        _generate(book, client)
    assert error.value.status == 412 and len(client.calls) == 1


def test_old_t1_report_and_strict_other_prompt_variables(home: Path) -> None:
    _, text = prompts.render("T1", {"题材方向": "都市", "市场观察": "无实时数据", "对标书": "无"})
    assert "〖" not in text and "报告" in text
    with pytest.raises(prompts.PromptError, match="未提供变量"):
        prompts.render("W1", {})
    with pytest.raises(prompts.PromptError, match="未提供变量"):
        prompts.render("T1", {})


def test_unsupported_book_t1_override_is_not_silently_bypassed(home: Path) -> None:
    book = _book(home)
    p = books.book_dir(book) / "prompts" / "generate" / "T1.md"
    old = (home / "prompts/generate/T1.md").read_text(encoding="utf-8").replace("interface_version: 1", "interface_version: 0")
    atomic.write_text(p, old)
    client = MockLLM()
    with pytest.raises(storyideas.StoryIdeasError) as error:
        _generate(book, client)
    assert error.value.status == 503 and client.calls == [] and p.read_text(encoding="utf-8") == old


@pytest.mark.parametrize("kind", ["empty", "json", "missing", "count", "duplicate", "blank", "long", "type", "genre", "version", "truncated"])
def test_invalid_model_output_no_retry_no_fake_cards(home: Path, kind: str) -> None:
    book = _book(home)
    class InvalidMock(MockLLM):
        def chat(self, **kwargs: Any) -> Any:
            res = super().chat(**kwargs)
            data = json.loads(res.text)
            if kind == "empty": res.text = ""
            elif kind == "json": res.text = "{broken"
            elif kind == "truncated": res.finish_reason = "length"
            else:
                if kind == "missing": del data["ideas"][0]["logline"]
                if kind == "count": data["ideas"].pop()
                if kind == "duplicate": data["ideas"][1]["logline"] = data["ideas"][0]["logline"] + "！"
                if kind == "blank": data["ideas"][0]["logline"] = "  "
                if kind == "long": data["ideas"][0]["title"] = "长" * 81
                if kind == "type": data["ideas"][0]["title"] = 1
                if kind == "genre": data["genre_template"] = "romance"
                if kind == "version": data["schema_version"] = True
                res.text = json.dumps(data, ensure_ascii=False)
            return res
    client = InvalidMock(); before = _files(book)
    with pytest.raises(storyideas.StoryIdeasError) as error:
        _generate(book, client)
    assert error.value.status == 502 and len(client.calls) == 1 and _files(book) == before


def test_same_book_concurrent_generation_rejected(home: Path) -> None:
    from ainovel.operations import BusyError
    book = _book(home)
    entered, release = threading.Event(), threading.Event()
    failures: list[Exception] = []
    class WaitingMock(MockLLM):
        def chat(self, **kwargs: Any) -> Any:
            entered.set(); assert release.wait(10)
            return super().chat(**kwargs)
    client = WaitingMock(); ctx = storyideas.context(book)
    def run() -> None:
        try: storyideas.generate(book, "", [], ctx["etag"], client=client)
        except Exception as exc: failures.append(exc)
    thread = threading.Thread(target=run); thread.start(); assert entered.wait(5)
    try:
        assert storyideas.context(book)["eligible"]  # 只读不等模型锁
        with pytest.raises(BusyError):
            storyideas.generate(book, "", [], ctx["etag"], client=MockLLM())
    finally:
        release.set(); thread.join(10)
    assert not failures and len(client.calls) == 1
