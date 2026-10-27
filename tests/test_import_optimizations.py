"""（规格05 F-08）导入编号、来源隔离、防覆盖及幂等回归；所有数据只落 home。"""
from __future__ import annotations

import codecs
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from ainovel import atomic, books, chapters, db, importer, llm, outline, pipeline, statemachine
from ainovel.mockllm import MockLLM


@pytest.fixture(autouse=True)
def offline(home: Path, monkeypatch: pytest.MonkeyPatch) -> MockLLM:
    """（规格05 §9.7）复用 home 隔离数据，模型工厂及直连均替换为离线 mock。"""
    client = MockLLM()
    monkeypatch.setattr(llm, "client", lambda: client)
    monkeypatch.setattr(llm.OpenAICompatClient, "chat", client.chat)
    return client


def _source(home: Path, titles: list[str], *, name: str = "导入.txt",
            encoding: str = "utf-8", bodies: list[str] | None = None) -> Path:
    """（规格05 §9.7）创建足字数的离线导入源，避免无关的短章告警。"""
    lines = []
    for i, title in enumerate(titles):
        lines.extend([title, bodies[i] if bodies is not None else f"正文{i}。" * 180])
    path = home / name
    path.write_text("\n".join(lines), encoding=encoding, newline="")
    return path


def _manifest(home: Path) -> dict[str, bytes]:
    """（规格05 §9.7）比较持久文件；SQLite 的读锁共享内存不属于档案内容。"""
    return {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*")
            if p.is_file() and not p.name.endswith(".db-shm")}


def _job(book: str, source: Path, **overrides: Any) -> tuple[Path, dict[str, Any]]:
    """（规格05 §9.6）读取当前源默认配置对应的断点凭据。"""
    text, enc = importer.read_text(source)
    config = {"file_fingerprint": importer._file_fingerprint(source),
              "regex": importer.guess_pattern(text), "encoding": enc, "mode": "draft"}
    config.update(overrides)
    path = importer._job_path(book, config["file_fingerprint"], config["regex"],
                              config["encoding"], config["mode"])
    return path, importer._load_job(path)


def test_actual_numbers_survive_preview_and_commit(home: Path, book_env: tuple[str, Path]) -> None:
    book, _ = book_env
    source = _source(home, ["第一章 初见", "第三章 重逢"])
    before = _manifest(home)
    preview = importer.preview(source)
    assert _manifest(home) == before
    assert [it["no"] for it in preview["chapters"]] == [1, 3]
    assert any(w["kind"] == "gap" for w in preview["warnings"])
    with pytest.raises(importer.ImportError_, match="警告未确认"):
        importer.commit(book, source)
    result = importer.commit(book, source, confirm_warnings=True)
    assert result["imported"] == 2
    assert chapters.list_chapters(book) == [1, 3]
    assert chapters.read_prose(book, 3)["title"] == "第三章 重逢"
    assert not chapters.prose_path(book, 2).exists()
    assert not chapters.outline_path(book, 3).exists()


def test_fallback_numbers_reserve_all_actual_numbers(home: Path, book_env: tuple[str, Path]) -> None:
    book, _ = book_env
    titles = ["序章 未编号", "第一章 正文", "番外 未编号", "第三章 正文", "尾声 未编号"]
    source = _source(home, titles)
    regex = r"^(?:序章|第.+章|番外|尾声).*"
    preview = importer.preview(source, regex)
    assert [it["no"] for it in preview["chapters"]] == [2, 1, 4, 3, 5]
    assert any(w["kind"] == "unparsed_no" for w in preview["warnings"])
    with pytest.raises(importer.ImportError_, match="章号无法解析"):
        importer.commit(book, source, regex=regex)
    importer.commit(book, source, regex=regex, confirm_warnings=True)
    assert chapters.list_chapters(book) == [1, 2, 3, 4, 5]
    assert chapters.read_prose(book, 2)["title"] == titles[0]


@pytest.mark.parametrize("confirm", [False, True])
def test_duplicate_actual_numbers_are_unconditionally_rejected(
        home: Path, book_env: tuple[str, Path], confirm: bool) -> None:
    book, _ = book_env
    source = _source(home, ["第一章 原章", "第1章 重复"])
    preview = importer.preview(source)
    assert [it["no"] for it in preview["chapters"]] == [1, 1]
    assert any(w["kind"] == "duplicate_no" for w in preview["warnings"])
    before = _manifest(home)
    with pytest.raises(importer.ImportError_, match="章号重复"):
        importer.commit(book, source, confirm_warnings=confirm)
    assert _manifest(home) == before
    assert statemachine.get_row(book, 1) is None


@pytest.mark.parametrize("kind", ["prose", "outline", "row", "snapshot", "draft", "memory"])
def test_preflight_checks_later_targets_before_any_write(
        home: Path, book_env: tuple[str, Path], kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    book, _ = book_env
    source = _source(home, ["第一章 新章", "第三章 已有"])
    if kind == "row":
        statemachine.ensure_chapter_row(book, 3, "历史标题")
        statemachine.set_current_version(book, 3, 7)
        statemachine.set_status_direct(book, 3, "finalized")
    elif kind == "snapshot":
        db.add_snapshot(book_id=books.book_id(book), chapter_no=3, version=7,
                        label="final", content="历史快照不可覆盖")
    else:
        path = {"prose": chapters.prose_path, "outline": chapters.outline_path,
                "draft": chapters.draft_path, "memory": chapters.memory_path}[kind](book, 3)
        atomic.write_text(path, "已有档案，禁止覆盖")
    before = _manifest(home)
    rows = statemachine.list_chapters(book)
    writer = Mock(wraps=chapters.write_prose)
    monkeypatch.setattr(chapters, "write_prose", writer)
    with pytest.raises(importer.ImportError_, match="拒绝覆盖.*3"):
        importer.commit(book, source, confirm_warnings=True)
    writer.assert_not_called()
    assert _manifest(home) == before
    assert statemachine.list_chapters(book) == rows
    assert not chapters.prose_path(book, 1).exists()


def test_three_identical_commits_preserve_files_job_and_snapshots(
        home: Path, book_env: tuple[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    book, _ = book_env
    source = _source(home, ["第一章 初见", "第二章 重逢"])
    assert importer.commit(book, source, mode="finalized")["imported"] == 2
    path, job = _job(book, source, mode="finalized")
    assert path.is_file() and job["done"] == [1, 2]
    assert all(k in job for k in ("file_fingerprint", "regex", "encoding", "mode"))
    before = _manifest(home)
    writer = Mock(side_effect=AssertionError("完成章节不能重写"))
    monkeypatch.setattr(chapters, "write_prose", writer)
    for _ in range(2):
        result = importer.commit(book, source, mode="finalized")
        assert (result["imported"], result["skipped"]) == (0, 2)
        assert path.is_file()
    writer.assert_not_called()
    assert _manifest(home) == before
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM snapshots WHERE book_id=?",
                            (books.book_id(book),)).fetchone()[0] == 2


@pytest.mark.parametrize("change", ["regex", "encoding", "mode", "source", "job_config"])
def test_different_source_or_configuration_cannot_reuse_done(
        home: Path, book_env: tuple[str, Path], change: str) -> None:
    book, _ = book_env
    source = _source(home, ["第一章 初见", "第二章 重逢"])
    importer.commit(book, source)
    old_path, old_job = _job(book, source)
    args: dict[str, Any] = {}
    if change == "regex":
        args["regex"] = r"^第.*章.*$"
    elif change == "encoding":
        args["encoding"] = "utf-8"
    elif change == "mode":
        args["mode"] = "finalized"
    elif change == "source":
        source.write_text(source.read_text(encoding="utf-8") + "新文字", encoding="utf-8", newline="")
    else:
        importer._save_job(old_path, old_job | {"mode": "finalized"})
    before = _manifest(home)
    with pytest.raises(importer.ImportError_, match="拒绝覆盖"):
        importer.commit(book, source, **args)
    assert _manifest(home) == before


@pytest.mark.parametrize("source_fp", [None, "different-source"])
def test_done_requires_matching_prose_source_fingerprint(
        home: Path, book_env: tuple[str, Path], source_fp: str | None) -> None:
    book, _ = book_env
    source = _source(home, ["第一章 初见", "第二章 重逢"])
    importer.commit(book, source)
    prose = chapters.read_prose(book, 2)
    body = prose.pop("_body")
    if source_fp is None:
        prose.pop("source_fingerprint")
    else:
        prose["source_fingerprint"] = source_fp
    chapters.write_prose(book, 2, prose, body)
    before = _manifest(home)
    with pytest.raises(importer.ImportError_, match="拒绝覆盖.*2"):
        importer.commit(book, source)
    assert _manifest(home) == before


def test_source_match_without_done_is_not_resumable(home: Path, book_env: tuple[str, Path]) -> None:
    book, _ = book_env
    source = _source(home, ["第一章 初见"])
    importer.commit(book, source)
    path, job = _job(book, source)
    importer._save_job(path, job | {"done": []})
    with pytest.raises(importer.ImportError_, match="拒绝覆盖"):
        importer.commit(book, source)


def test_same_content_in_new_path_resumes(home: Path, book_env: tuple[str, Path]) -> None:
    book, _ = book_env
    source = _source(home, ["第一章 初见"])
    importer.commit(book, source)
    moved = home / "另一文件名.txt"
    moved.write_bytes(source.read_bytes())
    assert importer.commit(book, moved)["skipped"] == 1


def test_resume_after_completed_chapter_does_not_rewrite_it(
        home: Path, book_env: tuple[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    book, _ = book_env
    source = _source(home, ["第一章 初见", "第二章 重逢", "第三章 终章"])
    save = importer._save_job

    def interrupt_after_first(path: Path, data: dict[str, Any]) -> None:
        save(path, data)
        raise RuntimeError("模拟进程中断")

    monkeypatch.setattr(importer, "_save_job", interrupt_after_first)
    with pytest.raises(RuntimeError, match="模拟进程中断"):
        importer.commit(book, source)
    assert _job(book, source)[1]["done"] == [1]
    first = chapters.prose_path(book, 1).read_bytes()
    monkeypatch.setattr(importer, "_save_job", save)
    writer = Mock(wraps=chapters.write_prose)
    monkeypatch.setattr(chapters, "write_prose", writer)
    result = importer.commit(book, source)
    assert (result["imported"], result["skipped"]) == (2, 1)
    assert [call.args[1] for call in writer.call_args_list] == [2, 3]
    assert chapters.prose_path(book, 1).read_bytes() == first
    assert _job(book, source)[1]["done"] == [1, 2, 3]


@pytest.mark.parametrize("mode", ["draft", "finalized"])
def test_row_exists_before_prose_and_metadata_matches(
        home: Path, book_env: tuple[str, Path], monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    book, _ = book_env
    source = _source(home, ["第三章 标题"])
    write = chapters.write_prose

    def checked_write(book: str, no: int, fm: dict[str, Any], body: str) -> None:
        row = statemachine.get_row(book, no)
        assert row is not None and row["title"] == "第三章 标题"
        assert row["current_version"] == 1
        write(book, no, fm, body)

    monkeypatch.setattr(chapters, "write_prose", checked_write)
    importer.commit(book, source, mode=mode, confirm_warnings=True)
    prose = chapters.read_prose(book, 3)
    row = statemachine.get_row(book, 3)
    assert row is not None
    assert prose["title"] == row["title"] == "第三章 标题"
    assert prose["version"] == row["current_version"] == 1
    assert prose["imported"] is True and row["imported"] == 1
    assert prose["source_fingerprint"] == importer._file_fingerprint(source)
    assert row["status"] == ("finalized" if mode == "finalized" else "pending_finalize")
    assert prose["status"] == ("final" if mode == "finalized" else "draft")
    assert row["checked_status"] is None  # 导入未运行机检，不能伪造 pass。
    with db.tx() as conn:
        snaps = conn.execute("SELECT * FROM snapshots WHERE book_id=? AND chapter_no=3",
                             (books.book_id(book),)).fetchall()
    assert len(snaps) == (1 if mode == "finalized" else 0)
    if snaps:
        assert snaps[0]["version"] == 1 and snaps[0]["label"] == "import"
        assert snaps[0]["content"] == prose["_body"]


@pytest.mark.parametrize(("number", "expected"), [
    ("十", 10), ("十二", 12), ("一百零三", 103), ("千", 1000),
    ("一千零一", 1001), ("两千三百四十五", 2345), ("九千九百九十九", 9999),
    ("一万零三", 10003), ("十万零一千零一", 101001),
    ("一亿零三万零七", 100030007), ("二〇二四", 2024), ("12345", 12345),
    ("", None), ("未知", None), ("一百猫", None), ("十百", None),
])
def test_common_chinese_chapter_numbers(home: Path, number: str, expected: int | None) -> None:
    assert importer.cn2num(number) == expected


def test_large_chinese_heading_and_gap_warning_are_bounded(home: Path, book_env: tuple[str, Path]) -> None:
    book, _ = book_env
    source = _source(home, ["第一千零一章 千章", "第一亿零三万零七章 亿章"])
    result = importer.preview(source)
    assert [it["no"] for it in result["chapters"]] == [1001, 100030007]
    assert len(str(result["warnings"])) < 500
    importer.commit(book, source, confirm_warnings=True)
    assert chapters.list_chapters(book) == [1001, 100030007]


@pytest.mark.parametrize(("codec", "bom"), [("utf-16-le", codecs.BOM_UTF16_LE),
                                             ("utf-16-be", codecs.BOM_UTF16_BE)])
def test_utf16_bom_takes_priority_over_permissive_fallback(
        home: Path, book_env: tuple[str, Path], codec: str, bom: bytes,
        monkeypatch: pytest.MonkeyPatch) -> None:
    book, _ = book_env
    source = home / "utf16.txt"
    text = "第一章 雨夜\n他推开门，雨灌进领口。" + "正文。" * 200
    raw = bom + text.encode(codec)
    source.write_bytes(raw)
    # 即使回退链包含可接受任意字节的编码，也必须先遵循 UTF-16 BOM。
    monkeypatch.setattr(importer, "ENCODINGS", ("latin-1", *importer.ENCODINGS))
    assert importer.detect_encoding(raw) == "utf-16"
    assert importer.read_text(source) == (text, "utf-16")
    assert importer.preview(source)["chapters"][0]["no"] == 1
    importer.commit(book, source)
    assert chapters.read_prose(book, 1)["_body"].startswith("他推开门，雨灌进领口。")


def test_preview_accepts_explicit_encoding_and_rejects_bad_settings(
        home: Path, book_env: tuple[str, Path]) -> None:
    book, _ = book_env
    source = _source(home, ["第一章 雨夜"], encoding="gb18030")
    assert importer.preview(source, encoding="gb18030")["encoding"] == "gb18030"
    for action in (importer.preview, lambda p, **kw: importer.commit(book, p, **kw)):
        with pytest.raises(importer.ImportError_, match="切章正则非法"):
            action(source, regex="[")
        with pytest.raises(importer.ImportError_, match="解码失败"):
            action(source, encoding="unknown-codec")


def test_empty_chapter_does_not_get_outline_or_memory(
        home: Path, book_env: tuple[str, Path], monkeypatch: pytest.MonkeyPatch, offline: MockLLM) -> None:
    book, _ = book_env
    source = _source(home, ["第十三章 空章", "第十五章 正文"], bodies=["", "正文。" * 200])
    rebuild = Mock(return_value={"ok": [15]})
    monkeypatch.setattr(pipeline, "rebuild_memory_batch", rebuild)
    result = importer.commit(book, source, mode="finalized", confirm_warnings=True,
                              rebuild_memory=True, client=offline)
    assert result["imported"] == 1 and result["issues"][0]["no"] == "13"
    assert not chapters.prose_path(book, 13).exists()
    assert statemachine.get_row(book, 13) is None
    rows = outline.read_outline(book)["chapter_rows"]
    assert "13" not in {r["章"] for r in rows}
    assert next(r for r in rows if r["章"] == "15")["本章目标"] == "（导入，无细纲）"
    rebuild.assert_called_once_with(book, [15], client=offline)


def test_legacy_resume_only_migrates_exact_default_draft(
        home: Path, book_env: tuple[str, Path]) -> None:
    book, _ = book_env
    source = _source(home, ["第一章 初见", "第二章 重逢"])
    text, _ = importer.read_text(source)
    first = importer.split_chapters(text, importer.guess_pattern(text))[0]
    fp = importer._file_fingerprint(source)
    importer._save_job(importer._job_path(book, fp), {"file_fingerprint": fp, "done": [1]})
    statemachine.ensure_chapter_row(book, 1, first["title"])
    chapters.write_prose(book, 1, {"chapter_no": 1, "version": 1, "status": "draft",
                                    "word_count": first["chars"]}, first["body"])
    statemachine.set_status_direct(book, 1, "pending_finalize")
    result = importer.commit(book, source)
    assert (result["imported"], result["skipped"]) == (1, 1)
    assert chapters.read_prose(book, 1)["source_fingerprint"] == fp
    assert _job(book, source)[1]["done"] == [1, 2]
    assert importer.commit(book, source)["skipped"] == 2


@pytest.mark.parametrize("change", ["body", "regex", "encoding", "mode", "fingerprint"])
def test_legacy_resume_does_not_assume_unverified_provenance(
        home: Path, book_env: tuple[str, Path], change: str) -> None:
    book, _ = book_env
    source = _source(home, ["第一章 初见", "第二章 重逢"])
    text, _ = importer.read_text(source)
    first = importer.split_chapters(text, importer.guess_pattern(text))[0]
    fp = importer._file_fingerprint(source)
    importer._save_job(importer._job_path(book, fp), {"file_fingerprint": fp, "done": [1]})
    statemachine.ensure_chapter_row(book, 1, first["title"])
    fm = {"chapter_no": 1, "version": 1, "status": "draft", "word_count": first["chars"]}
    if change == "fingerprint":
        fm["source_fingerprint"] = "other-source"
    chapters.write_prose(book, 1, fm, "人工修改" if change == "body" else first["body"])
    statemachine.set_status_direct(book, 1, "pending_finalize")
    args = {"regex": r"^第.*章.*$"} if change == "regex" else {}
    if change == "encoding":
        args["encoding"] = "utf-8"
    if change == "mode":
        args["mode"] = "finalized"
    before = _manifest(home)
    with pytest.raises(importer.ImportError_, match="拒绝覆盖"):
        importer.commit(book, source, **args)
    assert _manifest(home) == before


def test_job_identity_includes_every_partition_setting(home: Path) -> None:
    fp = "a" * 64
    paths = {importer._job_path("测试书", fp, "^第.*", "utf-8", "draft"),
             importer._job_path("测试书", fp, "^Chapter.*", "utf-8", "draft"),
             importer._job_path("测试书", fp, "^第.*", "gb18030", "draft"),
             importer._job_path("测试书", fp, "^第.*", "utf-8", "finalized"),
             importer._job_path("测试书", "b" * 64, "^第.*", "utf-8", "draft")}
    assert len(paths) == 5


@pytest.mark.parametrize("corruption", ["json", "unicode", "done", "prose"])
def test_corrupt_resume_data_never_overwrites_prose(
        home: Path, book_env: tuple[str, Path], corruption: str) -> None:
    book, _ = book_env
    source = _source(home, ["第一章 初见"])
    importer.commit(book, source)
    path, job = _job(book, source)
    if corruption == "json":
        atomic.write_text(path, "{bad json")
    elif corruption == "unicode":
        path.write_bytes(b"\xff\xfe\xff")
    elif corruption == "done":
        importer._save_job(path, job | {"done": 1})
    else:
        atomic.write_text(chapters.prose_path(book, 1), "损坏的正文 frontmatter")
    before = _manifest(home)
    with pytest.raises(importer.ImportError_, match="拒绝覆盖"):
        importer.commit(book, source)
    assert _manifest(home) == before


def test_no_heading_uses_warned_fallback_without_writing_preview(home: Path) -> None:
    source = home / "无标题.txt"
    source.write_text("没有章标题的正文。" * 100, encoding="utf-8", newline="")
    before = _manifest(home)
    result = importer.preview(source)
    assert result["chapters"][0]["no"] == 1
    assert {w["kind"] for w in result["warnings"]} >= {"no_heading", "unparsed_no"}
    assert _manifest(home) == before


def test_out_of_order_actual_numbers_do_not_invent_initial_gap(home: Path) -> None:
    source = _source(home, ["第三章 先出现", "第一章 后出现", "第二章 最后出现"])
    result = importer.preview(source)
    assert [it["no"] for it in result["chapters"]] == [3, 1, 2]
    assert not any(w["kind"] == "gap" for w in result["warnings"])


def test_completed_history_is_skipped_without_resetting_version_or_status(
        home: Path, book_env: tuple[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    book, _ = book_env
    source = _source(home, ["第一章 初见"])
    importer.commit(book, source)
    prose = chapters.read_prose(book, 1)
    prose["version"] = 4
    prose["status"] = "final"
    body = prose.pop("_body")
    chapters.write_prose(book, 1, prose, body + "人工修订")
    statemachine.set_current_version(book, 1, 4)
    statemachine.set_status_direct(book, 1, "finalized")
    db.add_snapshot(book_id=books.book_id(book), chapter_no=1, version=4,
                    label="final", content=body + "人工修订")
    writer = Mock(side_effect=AssertionError("导入不能重写已修订章节"))
    monkeypatch.setattr(chapters, "write_prose", writer)
    before = _manifest(home)
    result = importer.commit(book, source)
    assert (result["imported"], result["skipped"]) == (0, 1)
    writer.assert_not_called()
    assert _manifest(home) == before
    row = statemachine.get_row(book, 1)
    assert row is not None and row["current_version"] == 4 and row["status"] == "finalized"


@pytest.mark.parametrize("bom", [codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE])
def test_malformed_utf16_bom_does_not_fall_back_to_other_encodings(home: Path, bom: bytes) -> None:
    source = home / "损坏的utf16.txt"
    source.write_bytes(bom + b"\x00")
    assert importer.detect_encoding(source.read_bytes()) is None
    with pytest.raises(importer.ImportError_, match="编码无法识别"):
        importer.read_text(source)
