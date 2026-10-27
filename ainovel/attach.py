"""外部书稿挂载（规格05 F-07）：把任意已存在的书稿目录原地接管到书架。

设计要点：
- **原地接管，不搬不复制**：用户 `D:\\1-work` 下已有的项目目录可能很大、
  可能被别的工具引用，搬走会破坏它们。
- 挂载登记进 `data/attached_books.json`，`paths.book_dir()` 按它解析路径，
  于是所有既存接口（章节/大纲/统计）无需改动就直接对这本书生效。
- **安全硬拦**：盘符根、用户主目录、工作区根（如 `D:\\1-work` 本身）一律拒绝。
  这不是可配置项——误挂 + 误删是不可逆的。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from . import atomic, books, db
from .paths import (attached_books, books_dir, project_root, save_attached,
                    validate_book_name)

logger = logging.getLogger(__name__)

# 允许挂载的最小层级（盘符/根算 0 层）
MIN_DEPTH = 2

SCAFFOLD_FILES = {
    "meta.md": None,          # 由 _scaffold 生成
    "config.json": None,
    "STORY_BIBLE.md": None,
    "OUTLINE.md": None,
    "PLOT_POINTS.md": None,
    "LOCATIONS.md": None,
}


class AttachError(Exception):
    pass


def _depth(p: Path) -> int:
    return len([x for x in p.parts if x not in (os.sep, "/")]) - (1 if p.anchor else 0)


def validate_target(path: str | Path) -> Path:
    """四条安全硬拦 + 内容检查。返回规范化的绝对路径。"""
    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except OSError as e:
        raise AttachError(f"路径无法解析: {path}（{e}）") from e
    if not p.exists():
        raise AttachError(f"路径不存在: {p}")
    if not p.is_dir():
        raise AttachError(f"不是目录: {p}")
    if _depth(p) < MIN_DEPTH:
        raise AttachError(
            f"拒绝挂载层级过浅的目录（{_depth(p)} 层，至少 {MIN_DEPTH} 层）: {p}；"
            "疑似盘符根/工作区根，挂载后误删风险过高")
    if p == Path.home().resolve():
        raise AttachError(f"拒绝挂载用户主目录: {p}")
    if p == project_root().resolve():
        raise AttachError(f"拒绝挂载程序自身目录: {p}")
    if p == books_dir().resolve():
        raise AttachError(f"拒绝挂载内置书架根目录: {p}")
    files = list(p.glob("*.md")) + list(p.glob("*.txt"))
    nested = list(p.glob("*/*.md")) + list(p.glob("*/*.txt"))
    if not files and not nested:
        raise AttachError(f"目录下没有任何 .md / .txt 书稿: {p}")
    return p


def detect_layout(p: Path) -> dict[str, Any]:
    """结构探测：如实报告"没识别到什么"，缺失不能当成默认存在。"""
    has = lambda name: (p / name).exists()  # noqa: E731
    chapters_dir = p / "chapters"
    chapter_files = db._chapter_files(p)
    txt_files = sorted(p.glob("*.txt"))
    if has("config.json") and chapters_dir.exists():
        layout = "native"
    elif has(".soloent") or has("book.json"):
        layout = "novel-engine"
    elif (p / "大纲").exists() and (p / "正文").exists():
        layout = "oh-story"
    elif txt_files and not chapter_files:
        layout = "flat-txt"
    else:
        layout = "unknown"
    return {
        "layout": layout,
        "chapters": len(chapter_files),
        "txt_files": len(txt_files),
        "has_config": has("config.json"),
        "has_meta": has("meta.md"),
        "has_bible": has("STORY_BIBLE.md"),
        "has_outline": has("OUTLINE.md"),
        "has_plot_points": has("PLOT_POINTS.md"),
        "has_locations": has("LOCATIONS.md"),
        "has_characters": (p / "characters").is_dir(),
    }


def _backup_manifest(name: str, p: Path) -> Path:
    from .paths import data_dir

    d = data_dir() / ".attach-backup"
    d.mkdir(parents=True, exist_ok=True)
    items = []
    for f in sorted(p.rglob("*")):
        if f.is_file() and not any(part.startswith(".") for part in f.parts):
            try:
                items.append({"rel": str(f.relative_to(p)), "size": f.stat().st_size})
            except OSError:
                continue
            if len(items) >= 2000:  # 大目录不必全量入清单
                break
    ts = db.now().replace(":", "").replace("-", "")
    out = d / f"{name}-{ts}.json"
    atomic.write_text(out, json.dumps({"name": name, "path": str(p), "files": items},
                                      ensure_ascii=False, indent=2))
    return out


def _scaffold(p: Path, name: str, original_layout: str) -> list[str]:
    """只补符合规格01 §2 schema 的缺失文件，已存在的一个字节都不动。"""
    created: list[str] = []
    for sub in ("chapters", "characters"):
        d = p / sub
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(sub + "/")
    from . import frontmatter as FM

    if not (p / "meta.md").exists():
        meta = {
            "title": name, "author": "", "genre": "", "tags": [],
            "target_words": 1_000_000, "chapter_word_range": [2200, 2800],
            "platform": "", "status": "drafting",
        }
        FM.validate(meta, books.META_SCHEMA)
        atomic.write_text(p / "meta.md", FM.dump(
            meta, "（挂载的既有书稿：请补一句话故事核与卖点）\n"))
        created.append("meta.md")
    if not (p / "config.json").exists():
        atomic.write_text(p / "config.json", json.dumps({
            "genre_template": "urban",
            "model_stages": dict(books.DEFAULT_MODEL_STAGES),
            "chapter_word_range": [2200, 2800],
            "review_fix_max_rounds": 2,
            "assembly_budget_overrides": {},
            "external": True,
            "attached_at": db.now(),
            "original_layout": original_layout,
        }, ensure_ascii=False, indent=2))
        created.append("config.json")
    if not (p / "STORY_BIBLE.md").exists():
        sections = "\n".join(f"## {s}\n\n（待填）\n" for s in books.BIBLE_SECTIONS)
        atomic.write_text(p / "STORY_BIBLE.md", f"# 世界观圣经\n\n{sections}\n")
        created.append("STORY_BIBLE.md")
    if not (p / "OUTLINE.md").exists():
        from .outline import OUTLINE_SCHEMA

        fm = {"logline": "", "ending_direction": "", "volumes": []}
        FM.validate(fm, OUTLINE_SCHEMA)
        atomic.write_text(p / "OUTLINE.md", FM.dump(fm,
                          "## 章节总表\n\n"
                          "| 章 | 卷 | 本章目标 | 冲突来源 | 爽点/揭露 | 钩子类型 | 重要度★ |\n"
                          "|----|----|---------|---------|----------|---------|--------|\n\n"
                          "## 爽点节奏总谱\n\n（待规划）\n"))
        created.append("OUTLINE.md")
    if not (p / "PLOT_POINTS.md").exists():
        atomic.write_text(p / "PLOT_POINTS.md",
                          "# 伏笔台账\n\n| 编号 | 内容 | 埋设章 | 推进章 | 预计回收章 | 状态 | 备注 |\n"
                          "|------|------|--------|--------|-----------|------|------|\n")
        created.append("PLOT_POINTS.md")
    if not (p / "LOCATIONS.md").exists():
        atomic.write_text(p / "LOCATIONS.md",
                          "# 地点档案\n\n| 名称 | 特征 | 方位/距离 | 首现章 | 关联剧情 |\n"
                          "|------|------|----------|----------|----------|\n")
        created.append("LOCATIONS.md")
    return created


def attach(name: str, path: str | Path, scaffold: bool = True) -> dict[str, Any]:
    """原地挂载并同步索引（规格05 F-07）；同名历史只可由同一规范路径恢复。"""
    name = (name or "").strip()
    validate_book_name(name)  # 任何目标检查、备份、建目录、写库之前先守住路径穿越。
    p = validate_target(path)
    reg = attached_books()
    if name in reg:
        raise AttachError(f"该书已挂载: {name}")
    if (books_dir() / name).exists():
        raise AttachError(f"内置书架已存在同名书: {name}")
    with db.tx() as conn:
        row = conn.execute("SELECT path FROM books WHERE name=?", (name,)).fetchone()
    if row and Path(row["path"]).resolve() != p:
        raise AttachError(f"同名书保留着另一目录的运行历史，请使用其他书名: {name}")

    detected = detect_layout(p)  # 先探测原稿，不能把补齐结果冒充原始结构。
    backup = _backup_manifest(name, p)
    created = _scaffold(p, name, detected["layout"]) if scaffold else []
    reg[name] = str(p)
    save_attached(reg)
    try:
        db.rebuild_book(name)
    except Exception:
        reg.pop(name)
        save_attached(reg)
        raise

    return {"ok": True, "name": name, "path": str(p), "detected": detected,
            "scaffolded": created, "backup": str(backup)}


def detach(name: str, delete_files: bool = False) -> dict[str, Any]:
    """摘除（规格05 F-07）：只从活动书架移除，不删文件或不可恢复的运行历史。"""
    validate_book_name(name)
    reg = attached_books()
    if name not in reg:
        raise AttachError(f"该书不是外挂书: {name}")
    p = Path(reg[name])
    deleted = False
    if delete_files:
        # 硬规则：外挂目录永远不删。要清理由用户自己在资源管理器里做。
        raise AttachError("外挂书不支持删除文件（避免误删用户资料）；请仅摘除，目录由用户自行清理")
    reg.pop(name, None)
    save_attached(reg)
    # 摘除不是删除：保留主记录，避免级联丢掉不可恢复的运行历史。
    from . import fts

    with db.tx() as conn:
        fts.ensure_table(conn)
        row = conn.execute("SELECT id FROM books WHERE name=?", (name,)).fetchone()
        if row:
            conn.execute("DELETE FROM chapters_fts WHERE book_id=?", (row["id"],))
            conn.execute("UPDATE books SET detached=1 WHERE id=?", (row["id"],))
    return {"ok": True, "name": name, "path": str(p), "files_deleted": deleted}


def is_attached(name: str) -> bool:
    return name in attached_books()
