"""（规格05 C-02）同书写操作串行化，维护任务与全部写操作互斥。"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Iterator

from .paths import book_dir

_GUARD = threading.Lock()
_LOCKS: dict[str, Any] = {}
_ACTIVE = 0
_MAINTENANCE = False


class BusyError(RuntimeError):
    """同一本书已有生成或写入任务，拒绝重复请求。"""


@contextmanager
def writing(book: str) -> Iterator[None]:
    """（规格05 C-02）非阻塞书级互斥，允许同线程管线重入。"""
    global _ACTIVE
    key = str(book_dir(book).resolve()).casefold()
    with _GUARD:
        if _MAINTENANCE:
            raise BusyError("索引维护进行中，请稍后重试")
        lock = _LOCKS.setdefault(key, threading.RLock())
        if not lock.acquire(blocking=False):
            raise BusyError("本书已有写入/生成任务，请等待完成后刷新重试")
        _ACTIVE += 1
    try:
        yield
    finally:
        with _GUARD:
            _ACTIVE -= 1
            lock.release()


@contextmanager
def maintenance() -> Iterator[None]:
    """（规格01 §5.4）重建期间禁止并发生成/编辑，避免重置正在执行的状态。"""
    global _MAINTENANCE
    with _GUARD:
        if _ACTIVE or _MAINTENANCE:
            raise BusyError("已有生成/写入或维护任务，完成后再重建索引")
        _MAINTENANCE = True
    try:
        yield
    finally:
        with _GUARD:
            _MAINTENANCE = False


def serialized(fn: Callable[..., Any]) -> Callable[..., Any]:
    """（规格05 C-02）保护以 book 为首参的同步业务操作。"""
    @wraps(fn)
    def wrapped(book: str, *args: Any, **kwargs: Any) -> Any:
        with writing(book):
            return fn(book, *args, **kwargs)
    return wrapped
