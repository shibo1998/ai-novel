"""规格08 阶段A：规格07 收口的后端自动用例（§6.2 A-T04/A-T05/A-T06/A-T08）。

全部使用临时 AINOVEL_HOME 与 MockLLM，不读写真实 books/、data/，不联网。
A-T01/A-T02/A-T03/A-T07 是浏览器判据，证据见 `docs/规格08实施验收.md`。
"""
from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from ainovel import atomic, bible, books, characters, chapters, conflicts, outline
from ainovel import frontmatter as FM
from ainovel.api import app

client = TestClient(app)


G5_HEAD = """## 卷规划
| 卷号 | 卷名 | 起止章 | 核心冲突 | 本卷目标 | 卷末高潮 | 卷末钩子(出卷悬念) |
|---|---|---|---|---|---|---|
| 1 | 坊市 | 1-6 | 坊市争夺 | 立足 | 乱葬岗对决 | 身世线索 |

## 章节总表
| 章 | 卷 | 本章目标 | 冲突来源 | 爽点/揭露 | 钩子类型 | 重要度★ |
|---|---|---|---|---|---|---|
"""


def _g5_text(chapters_n: int = 6, fo_rows: list[str] | None = None) -> str:
    """拼一份 G5 形态输出：卷规划 + 章节总表 + 可选伏笔行 + 节奏总谱。"""
    lines = [G5_HEAD]
    for i in range(1, chapters_n + 1):
        lines.append(f"| {i} | 1 | 新目标{i} | 新冲突{i} | 新爽点{i} | 悬念 | ★3 |\n")
    rows = fo_rows or []
    lines.append("\n## 伏笔总表\n| 编号 | 内容 | 埋设章 | 推进章 | 预计回收章 | 备注 |\n")
    lines.append("|---|---|---|---|---|---|\n")
    lines.extend(rows)
    lines.append("\n## 爽点节奏总谱\n\n新节奏：1-2 小打脸，6 卷终。\n")
    return "".join(lines)


def _prose(book: str, no: int, body: str, status: str = "final") -> None:
    fm = {"chapter_no": no, "version": 1, "status": status,
          "word_count": len(body.replace("\n", "").replace(" ", "")), "finalized_at": None}
    atomic.write_text(chapters.prose_path(book, no), FM.dump(fm, body))


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def book_path(book: str, name: str):
    return books.book_dir(book) / name


# ---------------------------------------------------------------------------
# A-T04：已有正文章数按文件章号去重，不看表内状态列；受影响数取交集
# ---------------------------------------------------------------------------

def test_g5_preview_uses_actual_written_chapters(book_env):
    """2 章已定稿 + 1 章未定稿正文，且 3 章都在替换范围内 → 受影响 3 章。

    章节总表七列里**没有状态列**，所以任何"读表内状态"的实现都只能得到 0；
    这里同时断言表内确实没有状态列，防止实现回退。
    """
    book, _ = book_env
    _prose(book, 1, "第一章正文。")
    _prose(book, 2, "第二章正文。")
    _prose(book, 3, "第三章草稿正文。", status="draft")
    # 同一章再放一份半成品草稿：按章号去重，不能算两章
    atomic.write_text(chapters.draft_path(book, 3), "半成品")

    r = client.post(f"/api/books/{book}/preview/outline",
                    json={"content": _g5_text(chapters_n=6)})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["written"]["known"] is True
    assert body["written"]["numbers"] == [1, 2, 3]
    assert body["written"]["total"] == 3
    assert body["affected_numbers"] == [1, 2, 3]
    assert body["affected_total"] == 3
    # 表内没有状态列，"按状态列统计"在结构上不可能成立
    assert "状态" not in outline.CHAPTER_COLS

    # 替换范围之外（第 7 章起）不影响已写计数
    r2 = client.post(f"/api/books/{book}/preview/outline",
                     json={"content": _g5_text(chapters_n=9)})
    assert r2.json()["affected_total"] == 3


def test_g5_preview_marks_unknown_when_chapters_unreadable(book_env, monkeypatch):
    """章节目录读不出来 → 已知字段为 None 且带中文 warning，**不假计 0**。"""
    book, _ = book_env

    def boom(_book):
        raise OSError("权限")

    monkeypatch.setattr(chapters, "chapters_dir", boom)

    r = client.post(f"/api/books/{book}/preview/outline", json={"content": _g5_text()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["written"]["known"] is False
    assert body["written"]["total"] is None
    assert body["affected_total"] is None
    assert any("无法判定" in w for w in body["warnings"])


# ---------------------------------------------------------------------------
# A-T04：预览包含 PLOT_POINTS 的真实旧/新内容
# ---------------------------------------------------------------------------

def test_g5_preview_includes_plot_points_baseline(book_env):
    book, _ = book_env
    outline.write_plot_points(book, [
        {"编号": "FO-001", "内容": "玄铁令是钥匙", "埋设章": "2", "推进章": "5",
         "预计回收章": "40", "状态": "building", "备注": "主线"},
    ])
    keep_row = "| 玄铁令是钥匙 | 玄铁令是钥匙 | 2 | 5 | 40 | 主线 |\n"
    new_row = "| 新增伏笔 | 老周其实是内应 | 4 |  | 20 | 支线 |\n"
    before_outline = _sha(book_path(book, "OUTLINE.md"))
    before_plot = _sha(book_path(book, "PLOT_POINTS.md"))

    r = client.post(f"/api/books/{book}/preview/outline",
                    json={"content": _g5_text(fo_rows=[keep_row, new_row])})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["plot_readable"] is True
    assert "FO-001" in body["plot_points_text_old"]
    assert "building" in body["plot_points_text_old"]
    # 新内容 = 旧行保留（含原状态）+ 新行分配 FO-002
    assert "FO-001" in body["plot_points_text_new"]
    assert "FO-002" in body["plot_points_text_new"]
    assert body["foreshadows_kept"] == 1
    assert body["foreshadows_added"] == 1
    assert body["id_map"] == {"玄铁令是钥匙": "FO-001", "新增伏笔": "FO-002"}

    # 预览是纯计算：两个文件一个字节都没动
    assert _sha(book_path(book, "OUTLINE.md")) == before_outline
    assert _sha(book_path(book, "PLOT_POINTS.md")) == before_plot
    # 旧章行（12 章）对新章行（6 章）→ 第 7-12 章是删除
    assert body["removed_numbers"] == [7, 8, 9, 10, 11, 12]


def test_g5_preview_diff_matches_what_adopt_writes(book_env):
    """预览给出的新全文必须与真正采纳后落盘的内容一致（同一处渲染）。"""
    book, _ = book_env
    text = _g5_text(chapters_n=6)
    preview = client.post(f"/api/books/{book}/preview/outline", json={"content": text}).json()

    etag = client.get(f"/api/books/{book}/outline").headers["etag"]
    r = client.post(f"/api/books/{book}/adopt/outline", json={"content": text},
                    headers={"If-Match": etag})
    assert r.status_code == 200, r.text
    on_disk = book_path(book, "OUTLINE.md").read_text(encoding="utf-8")
    assert on_disk.strip() == preview["outline_text_new"].strip()


# ---------------------------------------------------------------------------
# A-T05 / A-T06：取消不写、组合版本校验
# ---------------------------------------------------------------------------

def test_g5_preview_and_cancel_write_nothing(book_env):
    """预览与"取消采纳"（只发预览、不发 adopt）都不改 OUTLINE/PLOT_POINTS。"""
    book, _ = book_env
    before = {n: _sha(book_path(book, n)) for n in ("OUTLINE.md", "PLOT_POINTS.md")}
    text = _g5_text(chapters_n=6, fo_rows=["| x | 新伏笔 | 1 |  |  |  |\n"])
    for _ in range(3):  # 多次预览同样不写
        assert client.post(f"/api/books/{book}/preview/outline",
                           json={"content": text}).status_code == 200
    after = {n: _sha(book_path(book, n)) for n in ("OUTLINE.md", "PLOT_POINTS.md")}
    assert after == before


def test_g5_plot_change_rejects_stale_adopt(book_env):
    """只改 PLOT_POINTS.md 也让旧 G5 采纳失效（组合版本），412 不覆盖。"""
    book, _ = book_env
    text = _g5_text(chapters_n=6)
    preview = client.post(f"/api/books/{book}/preview/outline", json={"content": text}).json()
    etag_before = client.get(f"/api/books/{book}/outline").headers["etag"]

    # 另一个标签页只改了伏笔台账
    outline.write_plot_points(book, [
        {"编号": "FO-001", "内容": "玄铁令是钥匙", "埋设章": "2", "推进章": "5",
         "预计回收章": "40", "状态": "building", "备注": "改过"},
    ])
    outline_hash = _sha(book_path(book, "OUTLINE.md"))

    r = client.post(f"/api/books/{book}/adopt/outline", json={"content": text},
                    headers={"If-Match": etag_before})
    assert r.status_code == 412, r.text
    # 被拒的采纳没有写 OUTLINE，候选内容也没被静默落盘
    assert _sha(book_path(book, "OUTLINE.md")) == outline_hash

    # 按当前版本重新预览 → 拿到新版本后可正常采纳
    fresh = client.post(f"/api/books/{book}/preview/outline", json={"content": text})
    assert fresh.status_code == 200
    etag_after = client.get(f"/api/books/{book}/outline").headers["etag"]
    assert etag_after != etag_before
    ok = client.post(f"/api/books/{book}/adopt/outline", json={"content": text},
                     headers={"If-Match": etag_after})
    assert ok.status_code == 200, ok.text


# ---------------------------------------------------------------------------
# A-T08：50 条裁批量——第 17 条明确失败、结果未知、重试不重复
# ---------------------------------------------------------------------------

def _seed_50(book: str) -> list[dict]:
    conflicts.enqueue([characters.Conflict(
        "new_setting", book, (i % 20) + 1, f"待补录设定：术法{i:02d}：效果{i}", None, None)
        for i in range(1, 51)])
    items = conflicts.list_open(book)
    assert len(items) == 50
    return items


def _term_rows(book: str, term: str) -> int:
    return bible.read(book)["术语表"].count(f"| {term} |")


def test_batch_50_explicit_failure_and_unknown_interrupt(book_env, monkeypatch):
    """逐项采纳 50 条：第 17 条写失败即停；已成功项不重跑；未执行项保持待处理。"""
    book, _ = book_env
    items = _seed_50(book)
    items.sort(key=lambda it: it["id"])
    real_write = bible.write

    def fail_on_index_17(_book, sections):
        if "| 术法17 |" in sections.get("术语表", ""):
            raise OSError("磁盘不可写")
        real_write(_book, sections)

    monkeypatch.setattr(bible, "write", fail_on_index_17)

    done, failed, unknown = [], [], []
    for idx, item in enumerate(items, 1):
        try:
            conflicts.resolve(item["id"], "adopt_ai")
            done.append(item["id"])
        except conflicts.ConflictWriteError:
            failed.append(item["id"])
            break          # 明确失败：前端停发后续项（规格08 §5）
        except Exception:
            unknown.append(item["id"])
            break          # 结果未知：先核验，不继续

    assert len(done) == 16
    assert len(failed) == 1
    assert not unknown
    assert _term_rows(book, "术法16") == 1
    assert _term_rows(book, "术法17") == 0

    # 已成功项不再出现在待处理队列；失败项仍在
    still_open = {it["id"] for it in conflicts.list_open(book)}
    assert failed[0] in still_open
    assert not (set(done) & still_open)
    # 未执行项一条都没动
    assert len(still_open) == 34

    # 重试只跑失败项 + 未执行项
    monkeypatch.setattr(bible, "write", real_write)
    retry = sorted(still_open)
    for cid in retry:
        conflicts.resolve(cid, "adopt_ai")
    terms = bible.read(book)["术语表"]
    for i in list(range(1, 17)) + [17]:
        assert terms.count(f"| 术法{i:02d} |") == 1, f"术法{i:02d} 重复写入"
    assert conflicts.list_open(book) == []


def test_batch_50_retry_does_not_duplicate_success(book_env, monkeypatch):
    """回执失败（写入成功但记录没落）重试一次不产生重复术语行。"""
    book, _ = book_env
    items = _seed_50(book)
    items.sort(key=lambda it: it["id"])
    real_save = conflicts._save
    calls = {"n": 0}

    def flaky_save(rows):
        calls["n"] += 1
        if calls["n"] == 7:          # 第 7 条回执写失败
            raise OSError("回执写入失败")
        real_save(rows)

    monkeypatch.setattr(conflicts, "_save", flaky_save)
    applied, receipt_failed = 0, None
    for item in items:
        try:
            conflicts.resolve(item["id"], "adopt_ai")
            applied += 1
        except conflicts.ConflictReceiptError:
            receipt_failed = item["id"]
            break
    assert applied == 6
    assert receipt_failed is not None
    assert _term_rows(book, "术法07") == 1        # 内容已写

    monkeypatch.setattr(conflicts, "_save", real_save)
    conflicts.resolve(receipt_failed, "adopt_ai")  # 幂等重试
    assert _term_rows(book, "术法07") == 1        # 没有第二行
    assert receipt_failed not in {it["id"] for it in conflicts.list_open(book)}
