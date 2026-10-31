"""（规格07 §5.2）档案资源的强 ETag 与 If-Match 前置条件。"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException, Response

def etag(paths: Path | Iterable[Path]) -> str:
    """按固定顺序纳入文件名、存在性与原始字节摘要，不依赖 mtime。"""
    items = [paths] if isinstance(paths, Path) else list(paths)
    digest = hashlib.sha256()
    for path in items:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        if not path.is_file():
            digest.update(b"missing\0")
            continue
        digest.update(b"file\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        digest.update(b"\0")
    return f'"sha256-{digest.hexdigest()}"'


def check(if_match: str | None, current: str) -> None:
    """旧客户端可省略；提供时必须精确匹配当前强标记。"""
    if if_match is not None and if_match.strip() != current:
        raise HTTPException(status_code=412, detail={
            "code": "RESOURCE_CHANGED",
            "message": "资源已变化，请保留本地修改，查看当前版本并重新合并",
        })


def expose(response: Response, value: str) -> None:
    response.headers["ETag"] = value
