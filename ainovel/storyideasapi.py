"""（规格11 §3）同题材故事候选API；只生成返回体，不采纳正式档案。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from . import storyideas

router = APIRouter(prefix="/api/books/{book}/story-ideas")


@router.get("/context", response_model=storyideas.StoryContext)
def context(book: str, response: Response) -> dict[str, Any]:
    try:
        result = storyideas.context(book)
    except storyideas.StoryIdeasError as error:
        raise HTTPException(error.status, {"code": error.code, "message": str(error)}) from None
    response.headers["ETag"] = result["etag"]
    return result


@router.post("/generate", response_model=storyideas.StoryResult)
def generate(book: str, payload: storyideas.GenerateInput, request: Request) -> dict[str, Any]:
    try:
        return storyideas.generate(book, payload.preferences, payload.previous_loglines, request.headers.get("if-match"))
    except storyideas.StoryIdeasError as error:
        raise HTTPException(error.status, {"code": error.code, "message": str(error)}) from None
