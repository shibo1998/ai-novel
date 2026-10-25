"""原子写：写临时文件 → os.replace（规格01 §5.1，防写一半崩溃损坏档案）。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_text(path: Path | str, content: str, *, encoding: str = "utf-8") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" 保留原样换行，避免 Windows 自动转 CRLF 造成 diff 噪音
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
