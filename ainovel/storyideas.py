"""（规格11 §2—§5）同题材故事灵感，仅返回候选，不发布正式档案。"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import books, frontmatter as FM, llm, models, outline, pipeline, production, prompts
from .mdtable import parse_table
from .operations import writing


class StoryIdeasError(RuntimeError):
    """（规格11 §3）稳定业务错误，不回显供应商连接和凭据信息。"""
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code, self.status = code, status


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StoryIdea(StrictModel):
    title: str = Field(min_length=1, max_length=80)
    logline: str = Field(min_length=1, max_length=400)
    synopsis: str = Field(min_length=1, max_length=1200)
    protagonist: str = Field(min_length=1, max_length=400)
    core_conflict: str = Field(min_length=1, max_length=800)
    selling_point: str = Field(min_length=1, max_length=800)
    volume_goal: str = Field(min_length=1, max_length=800)
    volume_hook: str = Field(min_length=1, max_length=800)

    @field_validator("*")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("候选字段不能为空白")
        return value.strip()


class GenerateInput(StrictModel):
    preferences: str = Field(default="", max_length=2000)
    previous_loglines: list[Annotated[str, Field(min_length=1, max_length=400)]] = Field(default_factory=list, max_length=3)

    @field_validator("previous_loglines")
    @classmethod
    def valid_previous(cls, value: list[str]) -> list[str]:
        if any(not text.strip() for text in value):
            raise ValueError("既有故事不能为空白")
        return value


class ModelBundle(StrictModel):
    schema_version: Literal[1]
    genre_template: str
    ideas: list[StoryIdea] = Field(min_length=3, max_length=3)


class StoryContext(StrictModel):
    schema_version: Literal[1]
    book: str
    genre_template: str
    genre: str
    eligible: bool
    reason: str
    etag: str


class StoryResult(StrictModel):
    schema_version: Literal[1]
    book: str
    genre_template: str
    genre: str
    context_etag: str
    request_id: str
    ideas: list[StoryIdea] = Field(min_length=3, max_length=3)


def context(book: str) -> dict[str, Any]:
    """（规格11 §3）只读上下文与文件指纹，不调用会保存任务的get/volumes。"""
    try:
        root = books.book_dir(book).resolve()
    except ValueError:
        raise StoryIdeasError("STORY_IDEAS_INPUT_INVALID", "书名不合法", 422) from None
    if not root.is_dir():
        raise StoryIdeasError("STORY_IDEAS_BOOK_NOT_FOUND", "书不存在，请返回书架", 404)
    try:
        cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
        genre = cfg.get("genre_template") if isinstance(cfg, dict) else None
        if not isinstance(genre, str) or genre not in books.GENRE_TEMPLATES:
            raise ValueError("题材配置不可用")
        FM.validate_file(root / "meta.md", books.META_SCHEMA)
        od = outline.read_outline(book)
        lines = od["_body"].splitlines()
        marker = next((i for i, line in enumerate(lines) if line.strip().startswith("|") and "本章目标" in line), None)
        if marker is None or marker + 1 >= len(lines):
            raise ValueError("章节表缺表头或分隔行")
        headers = [cell.strip() for cell in lines[marker].strip().strip("|").split("|")]
        separator = lines[marker + 1].strip()
        separators = [cell.strip() for cell in separator.strip().strip("|").split("|")] if separator.startswith("|") else []
        if headers != outline.CHAPTER_COLS or len(separators) != len(headers) or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separators):
            raise ValueError("章节表表头或分隔行无效")
        if not isinstance(od.get("volumes"), list) or any(not isinstance(value, dict) for value in od["volumes"]):
            raise ValueError("卷规划结构无效")
        chapter_files = sorted(p for p in (root / "chapters").rglob("*") if p.is_file())
        manifests = []
        pending = False
        for folder in sorted((root / "production").iterdir()) if (root / "production").exists() else []:
            if not folder.is_dir():
                continue
            job = production._load(book, folder.name)  # 共享严格文件校验，但不迁移/保存任务。
            manifests.append(folder / "manifest.json")
            pending = pending or job["status"] not in {"applied", "cancelled"}
        eligible = not (od.get("volumes") or od["chapter_rows"] or chapter_files or pending)
        reason = "" if eligible else "本书已有规划、章节或未结束卷任务，不能在此改换整本故事；请核对原卷工作台"
        files = [root / name for name in ("config.json", "meta.md", "OUTLINE.md")] + chapter_files + manifests
        fingerprints = {}
        for path in files:
            if not path.resolve().is_relative_to(root):
                raise ValueError("上下文路径越界")
            fingerprints[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        for pid in ("T1", "S0"):
            fingerprints["prompt:" + pid] = prompts.get(pid, book).digest
        etag = '"' + hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode("utf-8")).hexdigest() + '"'
        return {"schema_version": 1, "book": book, "genre_template": genre, "genre": books.GENRE_TEMPLATES[genre],
                "eligible": eligible, "reason": reason, "etag": etag}
    except (OSError, ValueError, UnicodeError, TypeError, FM.FrontmatterError, production.ProductionError, prompts.PromptError):
        raise StoryIdeasError("STORY_IDEAS_UNAVAILABLE", "书的题材、档案、任务或提示词不可读取，请修复后再生成故事", 503) from None


def _normal_logline(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if not c.isspace() and not unicodedata.category(c).startswith("P"))


def _parse_result(text: str, genre: str) -> list[StoryIdea]:
    """（规格11 §2/5）严格解析三份候选；认不出或重复不能冒充可用。"""
    try:
        match = re.fullmatch(r"\s*```(?:json)?\s*\n(.*?)\n```\s*", text, re.S)
        data = json.loads(match[1] if match else text)
        if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
            raise ValueError
        bundle = ModelBundle.model_validate(data)
        keys = [_normal_logline(idea.logline) for idea in bundle.ideas]
        if bundle.genre_template != genre or any(not key for key in keys) or len(set(keys)) != 3:
            raise ValueError
        return bundle.ideas
    except (ValueError, TypeError, ValidationError):
        raise StoryIdeasError("STORY_IDEAS_RESULT_INVALID", "模型未返回三份完整且不同的同题材故事；已有输入保留，请核对后显式重试", 502) from None


def _model_failure_message(book: str, prompt: Any, error: BaseException) -> str:
    """（规格11 §3）失败原因必须可诊断，否则"模型不存在"和"网络抖动"看起来一模一样。

    只带模型名、HTTP 状态码与白名单错误码（见 llm.error_brief），不透传响应体——
    供应商回显可能包含连接配置或凭据。
    """
    model = models.resolve(book, str(prompt.fm.get("model_stage") or "plan")).model
    brief = llm.error_brief(error)
    if "model_not_found" in brief:
        return (f"选题请求未完成：中转站没有模型「{model}」（{brief}）。"
                "请到设置页或档案页换成中转站支持的模型；本次未自动重试，可能仍产生费用")
    return (f"选题请求未完成（模型「{model}」，{brief}）；"
            "模型可能仍有费用，未自动重试，请核对后再决定")


def generate(book: str, preferences: str, previous_loglines: list[str], expected: str | None, *, client: Any = None) -> dict[str, Any]:
    """（规格11 §3）一次明确点击对应一次计账调用；返回后再核对上下文。"""
    try:
        payload = GenerateInput(preferences=preferences, previous_loglines=previous_loglines)
    except ValidationError:
        raise StoryIdeasError("STORY_IDEAS_INPUT_INVALID", "偏好或既有候选超长/类型不合法", 422) from None
    if expected is None:
        raise StoryIdeasError("STORY_IDEAS_PRECONDITION_REQUIRED", "请先读取当前题材与上下文版本", 428)
    with writing(book):
        ctx = context(book)
        if expected.strip() != ctx["etag"]:
            raise StoryIdeasError("STORY_IDEAS_CHANGED", "故事上下文已变化，请保留输入并重新核对", 412)
        if not ctx["eligible"]:
            raise StoryIdeasError("STORY_IDEAS_NOT_ELIGIBLE", ctx["reason"], 409)
        p = prompts.get("T1", book)
        if type(p.fm.get("interface_version")) is not int or p.fm["interface_version"] != 1:
            raise StoryIdeasError("STORY_IDEAS_PROMPT_UPGRADE_REQUIRED", "本书T1覆盖不支持故事候选接口；请人工升级，未绕过或覆盖模板", 503)
        variables = {"题材方向": ctx["genre_template"] + "（" + ctx["genre"] + "）", "市场观察": "未提供实时榜单或市场证据，不作热门/能爆保证",
                     "对标书": "无指定对标", "输出模式": "结构化故事候选", "作者偏好": payload.preferences or "无额外偏好，允许自由构思",
                     "既有候选摘要": json.dumps(payload.previous_loglines, ensure_ascii=False)}
        try:
            result, _, _ = pipeline._call(book, None, "story_ideas", "T1", variables, client=client, retry=False)
        except prompts.PromptError:
            raise StoryIdeasError("STORY_IDEAS_PROMPT_UNAVAILABLE", "选题模板无法完整渲染，请检查本书T1覆盖", 503) from None
        except Exception as error:
            raise StoryIdeasError("STORY_IDEAS_MODEL_FAILED",
                                  _model_failure_message(book, p, error), 502) from None
        if context(book)["etag"] != ctx["etag"]:
            raise StoryIdeasError("STORY_IDEAS_CHANGED", "生成期间故事上下文已改变，未采用过期候选；请保留输入重查", 412)
        if not result.stream_complete or result.finish_reason != "stop":
            raise StoryIdeasError("STORY_IDEAS_RESULT_INVALID", "模型结果未完整结束，未采用或自动重试", 502)
        ideas = _parse_result(result.text, ctx["genre_template"])
        return {"schema_version": 1, "book": book, "genre_template": ctx["genre_template"], "genre": ctx["genre"],
                "context_etag": ctx["etag"], "request_id": result.request_id, "ideas": [idea.model_dump() for idea in ideas]}
