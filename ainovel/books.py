"""书 CRUD：建目录结构 / config.json / meta.md（规格01 §1-§2.1）。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import atomic, db, frontmatter as FM
from .paths import book_dir, books_dir, validate_book_name

GENRE_TEMPLATES: dict[str, str] = {
    "urban": "都市",
    "xuanhuan": "玄幻",
    "romance": "言情",
    "suspense": "悬疑",
}

META_SCHEMA: FM.Schema = {
    "required": ["title", "genre", "target_words", "status"],
    "non_empty": ["title"],
    "types": {"title": str, "author": str, "genre": str, "target_words": int,
              "chapter_word_range": list, "platform": str, "status": str},
    "enums": {"status": ["drafting", "serializing", "done"]},
}

BIBLE_SECTIONS = ["基础设定", "力量体系", "势力格局", "历史脉络", "隐藏规则", "术语表", "日常细节"]

DEFAULT_MODEL_STAGES: dict[str, str] = {
    "plan": "deepseek-chat",
    "prose": "gemini-2.5-pro",
    "polish": "gemini-2.5-pro",
    "review": "deepseek-chat",
    "longctx": "gemini-2.5-pro",
}


def create_book(
    name: str,
    *,
    genre_template: str = "urban",
    target_words: int = 1_000_000,
    author: str = "",
    platform: str = "番茄",
    chapter_word_range: list[int] | None = None,
    model_stages: dict[str, str] | None = None,
) -> dict[str, Any]:
    name = name.strip()
    validate_book_name(name)
    bdir = book_dir(name)
    if bdir.exists():
        raise FileExistsError(f"书已存在: {name}")
    with db.tx() as conn:
        row = conn.execute("SELECT path, detached FROM books WHERE name=?", (name,)).fetchone()
    if row and (row["detached"] or Path(row["path"]).resolve() != bdir.resolve()):
        raise ValueError(f"书名保留着既有历史，请重新挂载原目录或使用其他书名: {name}")
    word_range = chapter_word_range or [2200, 2800]
    bdir.mkdir(parents=True)
    (bdir / "characters").mkdir()
    (bdir / "chapters").mkdir()

    meta_fm = {
        "title": name,
        "author": author,
        "genre": GENRE_TEMPLATES.get(genre_template, genre_template),
        "tags": [],
        "target_words": target_words,
        "chapter_word_range": word_range,
        "platform": platform,
        "benchmark_book": "",
        "compliance_note": "AI辅助创作，人工主导；本目录即创作日志证据链",
        "status": "drafting",
    }
    meta_md = FM.dump(meta_fm, "（待填：一句话故事核、核心卖点、金手指概述）\n")
    atomic.write_text(bdir / "meta.md", meta_md)

    config = {
        "genre_template": genre_template,
        "model_stages": dict(model_stages or DEFAULT_MODEL_STAGES),
        "chapter_word_range": word_range,
        "review_fix_max_rounds": 2,
        "assembly_budget_overrides": {},
    }
    atomic.write_text(bdir / "config.json", json.dumps(config, ensure_ascii=False, indent=2))

    bible_body = "\n".join(f"## {s}\n\n（待生成）\n" for s in BIBLE_SECTIONS)
    atomic.write_text(bdir / "STORY_BIBLE.md", f"# 世界观圣经\n\n{bible_body}\n")
    atomic.write_text(bdir / "OUTLINE.md", FM.dump(
        {"logline": "", "ending_direction": "", "volumes": []},
        "## 章节总表\n\n| 章 | 卷 | 本章目标 | 冲突来源 | 爽点/揭露 | 钩子类型 | 重要度★ |\n"
        "|----|----|---------|---------|----------|---------|--------|\n\n"
        "## 爽点节奏总谱\n\n（待规划）\n",
    ))
    atomic.write_text(bdir / "PLOT_POINTS.md",
                      "# 伏笔台账\n\n"
                      "| 编号 | 内容 | 埋设章 | 推进章 | 预计回收章 | 状态 | 备注 |\n"
                      "|------|------|--------|--------|-----------|------|------|\n")
    atomic.write_text(bdir / "LOCATIONS.md",
                      "# 地点档案\n\n"
                      "| 名称 | 特征 | 方位/距离 | 首现章 | 关联剧情 |\n"
                      "|------|------|----------|--------|----------|\n")

    with db.tx() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO books (name, path, created_at) VALUES (?,?,?)",
            (name, str(bdir), db.now()),
        )
    return {"name": name, "path": str(bdir)}


def list_books() -> list[dict[str, Any]]:
    from .paths import attached_books

    paths: dict[str, Any] = {}
    if books_dir().exists():
        for bdir in sorted(books_dir().glob("*")):
            if bdir.is_dir() and (bdir / "meta.md").exists():
                paths[bdir.name] = bdir
    external = attached_books()
    for name, raw in external.items():
        p = Path(raw)
        if p.is_dir():
            paths.setdefault(name, p)

    out: list[dict[str, Any]] = []
    for name in sorted(paths):
        try:
            item = read_meta(name) | {"name": name}
        except Exception as e:  # 外挂书可能没有 meta.md 或格式不同：如实报告，不整架打不开
            item = {"name": name, "title": name, "genre": "", "target_words": 0,
                    "status": "unknown", "_error": str(e)}
        item["external"] = name in external
        try:
            from . import progress

            item.update(progress.derive(name))
        except Exception as error:
            item.update({"chapters_done": None, "chapters_total": None, "words_done": None,
                         "last_edited_at": None,
                         "progress_warnings": [{"code": "PROGRESS_UNKNOWN",
                                                "message": f"进度统计失败：{error}"}]})
        out.append(item)
    # 时间降序、同时间书名稳定升序；None 始终置后。
    out.sort(key=lambda item: item["name"])
    out.sort(key=lambda item: item.get("last_edited_at") or "", reverse=True)
    return out


def delete_book(name: str) -> None:
    """删除 = 删整个 books/<书名>/ 目录（API 层负责二次确认）。

    外挂书**不允许**走这里：它的目录在书架之外，可能是用户多年的稿子，
    误删不可逆。要移除请用 detach（只摘记录不删文件）。
    """
    from . import fts
    from .paths import attached_books

    if name in attached_books():
        raise ValueError(f"{name} 是外挂书，不能删除文件；请用『摘除』（只从书架移除）")
    bdir = book_dir(name)
    with db.tx() as conn:
        row = conn.execute("SELECT path, detached FROM books WHERE name=?", (name,)).fetchone()
    if row and (row["detached"] or Path(row["path"]).resolve() != bdir.resolve()):
        raise ValueError(f"书名保留着既有历史，不能通过删除同名内置目录清掉外挂历史: {name}")
    if not bdir.exists():
        raise FileNotFoundError(name)
    if bdir.parent != books_dir():
        # 双保险：book_dir 已校验书名，这里再断言目录确实落在 books/ 下
        raise ValueError(f"非法书目录: {bdir}")
    shutil.rmtree(bdir)
    with db.tx() as conn:
        fts.ensure_table(conn)  # FTS 表懒创建：从未入库时先确保存在
        row = conn.execute("SELECT id FROM books WHERE name=?", (name,)).fetchone()
        if row:
            conn.execute("DELETE FROM chapters_fts WHERE book_id=?", (row["id"],))
            conn.execute("DELETE FROM books WHERE id=?", (row["id"],))


def read_meta(name: str) -> dict[str, Any]:
    fm, body = FM.validate_file(book_dir(name) / "meta.md", META_SCHEMA)
    return fm | {"_body": body}


def write_meta(name: str, fm: dict[str, Any], body: str | None = None) -> None:
    FM.validate(fm, META_SCHEMA)
    _, old_body = FM.read_file(book_dir(name) / "meta.md")
    atomic.write_text(book_dir(name) / "meta.md", FM.dump(fm, body if body is not None else old_body))


def load_config(name: str) -> dict[str, Any]:
    path = book_dir(name) / "config.json"
    if not path.exists():
        raise FileNotFoundError(f"config.json 不存在: {path}")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    defaults = {
        "genre_template": "urban",
        "model_stages": {},
        "chapter_word_range": [2200, 2800],
        "review_fix_max_rounds": 2,
        "assembly_budget_overrides": {},
    }
    for k, v in defaults.items():
        cfg.setdefault(k, v)
    return cfg


def save_config(name: str, cfg: dict[str, Any]) -> None:
    atomic.write_text(book_dir(name) / "config.json", json.dumps(cfg, ensure_ascii=False, indent=2))


def book_id(name: str) -> int:
    """取得活动书 ID（规格05 F-07）；摘除行只保留历史，不可被普通接口复活。"""
    bdir = book_dir(name)
    with db.tx() as conn:
        row = conn.execute("SELECT id, path, detached FROM books WHERE name=?", (name,)).fetchone()
        if row is not None:
            if row["detached"]:
                raise FileNotFoundError(f"书已从书架摘除: {name}")
            if Path(row["path"]).resolve() != bdir.resolve():
                raise ValueError(f"书名对应的路径与历史不一致: {name}")
            return row["id"]
        if not bdir.is_dir():
            raise FileNotFoundError(name)
        # 文件为准：删库后允许活动书自动补行。
        conn.execute(
            "INSERT INTO books (name, path, created_at) VALUES (?,?,?)",
            (name, str(bdir), db.now()),
        )
        return conn.execute("SELECT id FROM books WHERE name=?", (name,)).fetchone()["id"]
