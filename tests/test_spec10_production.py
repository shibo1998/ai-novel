"""（规格10 阶段A）文件任务与人工联合确认；发动机以离线候选替身隔离。"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ainovel import api, atomic, books, chapters, frontmatter as FM, outline, production, statemachine
from ainovel.mockllm import MockLLM


def request(**changes: Any) -> dict[str, Any]:
    return {"mode": "prepare", "volume_no": 1, "count": 3, "logline": "临时故事", "requirements": "", "client_request_id": str(uuid4())} | changes


def make_bundle(book: str, req: dict[str, Any]) -> dict[str, Any]:
    od = outline.read_outline(book)
    fm = {k: v for k, v in od.items() if not k.startswith("_") and k != "chapter_rows"}
    rows = [row for row in od["chapter_rows"] if int(row["章"]) not in req["numbers"]]
    rows += [{"章": str(no), "卷": str(req["volume_no"]), "本章目标": f"目标{no}", "冲突来源": "风雨", "爽点/揭露": "发现", "钩子类型": "悬念", "重要度★": "3"} for no in req["numbers"]]
    return {"schema_version": 1, "outline": {"fm": fm, "rows": rows, "rhythm": od["_rhythm"]}, "files": {},
            "outlines": [{"no": no, "fm": FM.parse(MockLLM()._default_outline(no))[0], "body": "临时场景"} for no in req["numbers"]], "notes": []}


def validate(book: str, bundle: dict[str, Any], numbers: list[int]) -> dict[str, str]:
    assert bundle["schema_version"] == 1
    if [item["no"] for item in bundle["outlines"]] != numbers:
        raise production.ProductionError("PRODUCTION_PARTIAL", "候选章号不完整")
    if set(numbers) & set(__import__("ainovel.planning", fromlist=["protected_numbers"]).protected_numbers(book)):
        raise production.ProductionError("PRODUCTION_PROTECTED", "含受保护章")
    files = {"OUTLINE.md": outline.render_outline(**bundle["outline"])}
    for item in bundle["outlines"]:
        assert item["fm"]["confirmed"] is False
        files[f"chapters/{item['no']:03d}_细纲.md"] = FM.dump(item["fm"], item["body"])
    return files


@pytest.fixture
def engine(monkeypatch: Any) -> ModuleType:
    fake = ModuleType("ainovel.volumeproduction")
    fake.prepare = lambda book, req, **kwargs: make_bundle(book, req)
    fake.validate_bundle = validate
    fake.generate_chapter = lambda *_args, **_kwargs: (_ for _ in ()).throw(production.ProductionError("TEST_PAUSE", "测试只确认不出稿"))
    monkeypatch.setitem(sys.modules, "ainovel.volumeproduction", fake)
    import ainovel
    monkeypatch.setattr(ainovel, "volumeproduction", fake, raising=False)
    yield fake
    production._ACTIVE.clear()
    production._CONTROLS.clear()


def start(book: str, **kwargs: Any) -> dict[str, Any]:
    return production.create(book, request(**kwargs), production.book_etag(book), background=False)


def test_prepare_has_no_automatic_adoption(book_env: Any, engine: Any) -> None:
    book, home = book_env
    before = {str(path): path.read_bytes() for path in (home / "books" / book).rglob("*") if path.is_file()}
    job = start(book)
    assert job["status"] == "awaiting_outline_confirmation"
    assert all(path.read_bytes() == raw for path, raw in [(Path(name), data) for name, data in before.items()])
    assert all(not chapters.outline_path(book, no).exists() for no in job["numbers"])
    assert production.review(book, job["id"])["files"]
    production.control(book, job["id"], "cancel", job["etag"])
    assert not chapters.prose_path(book, 1).exists()


def test_volume_confirmation_is_one_explicit_action(book_env: Any, engine: Any) -> None:
    book, _ = book_env
    job = start(book)
    rid = str(uuid4())
    result = production.confirm(book, job["id"], job["preview_digest"], rid, job["etag"], background=False)
    assert result["status"] == "paused"
    assert all(chapters.read_outline_contract(book, no)["confirmed"] for no in job["numbers"])
    assert all(statemachine.current_status(book, no) == "outline_locked" for no in job["numbers"])
    assert all(not chapters.prose_path(book, no).exists() for no in job["numbers"])
    again = production.confirm(book, job["id"], job["preview_digest"], rid, job["etag"], background=False)
    assert again == result


def test_confirmation_rejects_stale_protected_or_partial_bundle(book_env: Any, engine: Any) -> None:
    book, _ = book_env
    job = start(book)
    bundle = production.review(book, job["id"])["candidate"]
    bad = copy.deepcopy(bundle)
    bad["outlines"].pop()
    with pytest.raises(production.ProductionError):
        production.update_candidate(book, job["id"], bad, job["etag"])
    bundle["notes"].append("编辑后的候选")
    changed = production.update_candidate(book, job["id"], bundle, job["etag"])["job"]
    with pytest.raises(production.ProductionError):
        production.confirm(book, job["id"], job["preview_digest"], str(uuid4()), changed["etag"], background=False)
    atomic.write_text(books.book_dir(book) / "STORY_BIBLE.md", "## 基础设定\n改动")
    with pytest.raises(production.ProductionError):
        production.confirm(book, job["id"], changed["preview_digest"], str(uuid4()), changed["etag"], background=False)
    assert not chapters.outline_path(book, 1).exists()


def test_job_idempotency_and_same_book_busy(book_env: Any, engine: Any) -> None:
    book, _ = book_env
    payload = request()
    first = production.create(book, payload, production.book_etag(book), background=False)
    assert production.create(book, payload, '"old"', background=False)["id"] == first["id"]
    with pytest.raises(production.ProductionError, match="参数"):
        production.create(book, payload | {"count": 2}, production.book_etag(book), background=False)
    with pytest.raises(production.ProductionError, match="未完成"):
        start(book)


def test_append_keeps_written_chapters_and_numbers(book_env: Any, engine: Any) -> None:
    book, _ = book_env
    original = outline.read_outline(book)
    job = start(book, mode="append", count=2)
    assert job["numbers"] == [13, 14]
    assert outline.read_outline(book) == original
    production.control(book, job["id"], "cancel", job["etag"])
    od = outline.read_outline(book)
    od["volumes"].append({"no": 2, "name": "后卷", "chapters": [13, 16]})
    outline.write_outline(book, {key: value for key, value in od.items() if not key.startswith("_") and key != "chapter_rows"}, od["chapter_rows"], od["_rhythm"])
    with pytest.raises(production.ProductionError, match="后卷冲突"):
        start(book, mode="append", count=2)
    new = start(book, mode="new_volume", count=2)
    assert new["numbers"] == [17, 18] and new["request"]["volume_no"] == 3


def test_restart_preserves_files_and_never_auto_rebills(book_env: Any, engine: Any, monkeypatch: Any) -> None:
    book, _ = book_env
    calls = []
    monkeypatch.setattr(production, "_launch", lambda *_: calls.append("launch"))
    job = production.create(book, request(), production.book_etag(book))
    assert job["status"] == "preparing"
    production._ACTIVE.clear()
    assert production.get(book, job["id"])["status"] == "interrupted"
    assert calls == ["launch"]
    assert (production.directory(book, job["id"]) / "manifest.json").exists()


def test_confirmation_fault_restores_original_files_and_rows(book_env: Any, engine: Any, monkeypatch: Any) -> None:
    book, _ = book_env
    job = start(book)
    original = (books.book_dir(book) / "OUTLINE.md").read_bytes()
    write = atomic.write_text
    once = [False]
    def fail(path: Path, text: str, **kwargs: Any) -> None:
        if Path(path).name == "002_细纲.md" and not once[0]:
            once[0] = True
            raise OSError("写入故障")
        write(path, text, **kwargs)
    monkeypatch.setattr(atomic, "write_text", fail)
    with pytest.raises(OSError):
        production.confirm(book, job["id"], job["preview_digest"], str(uuid4()), job["etag"], background=False)
    assert (books.book_dir(book) / "OUTLINE.md").read_bytes() == original
    assert all(not chapters.outline_path(book, no).exists() and statemachine.get_row(book, no) is None for no in job["numbers"])
    assert production.get(book, job["id"])["status"] == "awaiting_outline_confirmation"


def test_invalid_manifest_is_not_zero_jobs(book_env: Any, engine: Any) -> None:
    book, _ = book_env
    job = start(book)
    atomic.write_text(production.directory(book, job["id"]) / "manifest.json", '{"schema_version":99}')
    assert production.volumes(book)["warnings"]
    with pytest.raises(production.ProductionError, match="修复"):
        start(book)


def test_api_preconditions_and_manual_flags(book_env: Any, engine: Any) -> None:
    book, _ = book_env
    client = TestClient(api.app)
    base = f"/api/books/{book}"
    assert client.get(base + "/volumes").status_code == 200
    assert client.post(base + "/production", json=request()).status_code == 428
    assert client.post(base + "/production", json=request(count=True)).status_code == 422
    assert client.post(base + "/production", json=request(auto_finalize=True)).status_code == 422
    assert client.post(base + f"/production/{uuid4()}/finalize", json={"client_request_id": str(uuid4()), "chapter_nos": [1], "fingerprints": {"1": "x"}, "confirmed": False}).status_code == 422


def test_unwritten_confirmed_contract_change_stops_resume(book_env: Any, engine: Any) -> None:
    book, _ = book_env
    job = start(book)
    job = production.confirm(book, job["id"], job["preview_digest"], str(uuid4()), job["etag"], background=False)
    path = chapters.outline_path(book, 3)
    atomic.write_text(path, path.read_text(encoding="utf-8") + "\n外部修改。")
    assert production.get(book, job["id"])["status"] == "stale"
    with pytest.raises(production.ProductionError):
        production.control(book, job["id"], "resume", production.get(book, job["id"])["etag"], rid=str(uuid4()), background=False)


def test_confirmation_recovery_never_overwrites_third_party(book_env: Any, engine: Any, monkeypatch: Any) -> None:
    book, _ = book_env
    job = start(book)
    original_write = atomic.write_text
    once = [False]
    def collide(path: Path, text: str, **kwargs: Any) -> None:
        if Path(path).name == "002_细纲.md" and not once[0]:
            once[0] = True
            original_write(books.book_dir(book) / "OUTLINE.md", "第三方档案")
            raise OSError("模拟中断")
        original_write(path, text, **kwargs)
    monkeypatch.setattr(atomic, "write_text", collide)
    with pytest.raises(production.ProductionError, match="其他操作"):
        production.confirm(book, job["id"], job["preview_digest"], str(uuid4()), job["etag"], background=False)
    assert (books.book_dir(book) / "OUTLINE.md").read_text(encoding="utf-8") == "第三方档案"
    assert production.get(book, job["id"])["status"] == "recovery_required"


def test_confirmation_receipt_failure_keeps_recovery(book_env: Any, engine: Any, monkeypatch: Any) -> None:
    book, _ = book_env
    job = start(book)
    original = (books.book_dir(book) / "OUTLINE.md").read_bytes()
    original_save = production._save
    once = [False]
    def fail_receipt(book: str, manifest: dict[str, Any]) -> None:
        if manifest.get("confirmation_digest") and not once[0]:
            once[0] = True
            raise OSError("回执写失败")
        original_save(book, manifest)
    monkeypatch.setattr(production, "_save", fail_receipt)
    with pytest.raises(OSError):
        production.confirm(book, job["id"], job["preview_digest"], str(uuid4()), job["etag"], background=False)
    assert (books.book_dir(book) / "OUTLINE.md").read_bytes() == original
    assert all(not chapters.outline_path(book, no).exists() for no in job["numbers"])
    assert production.get(book, job["id"])["status"] == "awaiting_outline_confirmation"


def test_remove_deletes_finished_job_only(book_env: Any, engine: Any) -> None:
    """（规格10 追加）已结束任务可删除：任务目录消失，章节稿件不受影响。"""
    book, _ = book_env
    job = start(book)
    path = production.directory(book, job["id"])
    assert path.exists()
    assert production.remove(book, job["id"])["ok"] is True
    assert not path.exists()
    assert all(row["id"] != job["id"] for row in production.volumes(book)["jobs"])
    with pytest.raises(production.ProductionError) as error:
        production.remove(book, job["id"])
    assert error.value.status == 404


def test_remove_refuses_running_job(book_env: Any, engine: Any) -> None:
    """运行中的任务必须先停止后续生产，不能直接删。"""
    book, _ = book_env
    job = start(book)
    manifest = production._load(book, job["id"])
    manifest["status"] = "generating"
    production._save(book, manifest)
    with pytest.raises(production.ProductionError) as error:
        production.remove(book, job["id"])
    assert error.value.code == "PRODUCTION_RUNNING" and error.value.status == 409
    assert production.directory(book, job["id"]).exists()


def test_remove_job_api(book_env: Any, engine: Any) -> None:
    """（规格10 追加）DELETE 路由：已结束 200，重复删除 404。"""
    book, _ = book_env
    client = TestClient(api.app)
    job = start(book)
    assert client.delete(f"/api/books/{book}/production/{job['id']}").status_code == 200
    assert client.delete(f"/api/books/{book}/production/{job['id']}").status_code == 404


def test_remove_deletes_finished_job_only(book_env: Any, engine: Any) -> None:
    """（规格10 追加）已结束任务可删除：任务目录消失，章节稿件不受影响。"""
    book, _ = book_env
    job = start(book)
    path = production.directory(book, job["id"])
    assert path.exists()
    assert production.remove(book, job["id"])["ok"] is True
    assert not path.exists()
    assert all(row["id"] != job["id"] for row in production.volumes(book)["jobs"])
    with pytest.raises(production.ProductionError) as error:
        production.remove(book, job["id"])
    assert error.value.status == 404


def test_remove_refuses_running_job(book_env: Any, engine: Any) -> None:
    """运行中的任务必须先停止后续生产，不能直接删。"""
    book, _ = book_env
    job = start(book)
    manifest = production._load(book, job["id"])
    manifest["status"] = "generating"
    production._save(book, manifest)
    with pytest.raises(production.ProductionError) as error:
        production.remove(book, job["id"])
    assert error.value.code == "PRODUCTION_RUNNING" and error.value.status == 409
    assert production.directory(book, job["id"]).exists()


def test_remove_job_api(book_env: Any, engine: Any) -> None:
    """（规格10 追加）DELETE 路由：已结束 200，重复删除 404。"""
    book, _ = book_env
    client = TestClient(api.app)
    job = start(book)
    assert client.delete(f"/api/books/{book}/production/{job['id']}").status_code == 200
    assert client.delete(f"/api/books/{book}/production/{job['id']}").status_code == 404
