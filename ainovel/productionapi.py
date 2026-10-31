"""（规格10 §3）卷工作台任务API；旧单章端点保持兼容。"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from . import production

router = APIRouter(prefix="/api/books/{book}")


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreatePayload(StrictPayload):
    mode: Literal["prepare", "append", "new_volume"] = "prepare"
    volume_no: StrictInt | None = Field(default=None, ge=1)
    count: StrictInt = Field(default=10, ge=1, le=50)
    logline: str = Field(default="", max_length=4000)
    requirements: str = Field(default="", max_length=12000)
    client_request_id: str


class RequestPayload(StrictPayload):
    client_request_id: str


class ConfirmPayload(RequestPayload):
    preview_digest: str


class CandidatePayload(StrictPayload):
    candidate: dict[str, Any]


class FinalizePayload(RequestPayload):
    chapter_nos: list[StrictInt]
    fingerprints: dict[str, str]
    confirmed: Literal[True]


def _expose(response: Response, job: dict[str, Any]) -> dict[str, Any]:
    response.headers["ETag"] = job["etag"]
    return job


@router.get("/volumes")
def get_volumes(book: str, response: Response) -> dict[str, Any]:
    result = production.volumes(book)
    response.headers["ETag"] = result["etag"]
    return result


@router.post("/production", status_code=202)
def create(book: str, payload: CreatePayload, request: Request, response: Response) -> dict[str, Any]:
    return _expose(response, production.create(book, payload.model_dump(exclude_none=True), request.headers.get("if-match")))


@router.delete("/production/{job_id}")
def remove(book: str, job_id: str) -> dict[str, Any]:
    """（规格10 追加）删除已结束的任务目录；不动章节稿件与生成日志。"""
    return production.remove(book, job_id)


@router.get("/production/{job_id}")
def get_job(book: str, job_id: str, response: Response) -> dict[str, Any]:
    return _expose(response, production.get(book, job_id))


@router.get("/production/{job_id}/review")
def review(book: str, job_id: str, response: Response) -> dict[str, Any]:
    result = production.review(book, job_id)
    response.headers["ETag"] = result["job"]["etag"]
    return result


@router.put("/production/{job_id}/candidate")
def update(book: str, job_id: str, payload: CandidatePayload, request: Request, response: Response) -> dict[str, Any]:
    result = production.update_candidate(book, job_id, payload.candidate, request.headers.get("if-match"))
    response.headers["ETag"] = result["job"]["etag"]
    return result


@router.post("/production/{job_id}/confirm", status_code=202)
def confirm(book: str, job_id: str, payload: ConfirmPayload, request: Request, response: Response) -> dict[str, Any]:
    return _expose(response, production.confirm(book, job_id, payload.preview_digest, payload.client_request_id, request.headers.get("if-match")))


@router.post("/production/{job_id}/pause")
def pause(book: str, job_id: str, request: Request, response: Response, payload: StrictPayload) -> dict[str, Any]:
    return _expose(response, production.control(book, job_id, "pause", request.headers.get("if-match")))


@router.post("/production/{job_id}/cancel")
def cancel(book: str, job_id: str, request: Request, response: Response, payload: StrictPayload) -> dict[str, Any]:
    return _expose(response, production.control(book, job_id, "cancel", request.headers.get("if-match")))


@router.post("/production/{job_id}/resume", status_code=202)
def resume(book: str, job_id: str, payload: RequestPayload, request: Request, response: Response) -> dict[str, Any]:
    return _expose(response, production.control(book, job_id, "resume", request.headers.get("if-match"), rid=payload.client_request_id))


@router.post("/production/{job_id}/recover")
def recover(book: str, job_id: str, request: Request, response: Response, payload: StrictPayload) -> dict[str, Any]:
    production.check_version(request.headers.get("if-match"), production.get(book, job_id)["etag"])
    return _expose(response, production.recover(book, job_id))


@router.post("/production/{job_id}/finalize")
def finalize(book: str, job_id: str, payload: FinalizePayload, request: Request, response: Response) -> dict[str, Any]:
    result = production.finalize(book, job_id, payload.chapter_nos, payload.fingerprints, payload.client_request_id, request.headers.get("if-match"))
    response.headers["ETag"] = result["job"]["etag"]
    return result
