from __future__ import annotations

import atexit
import concurrent.futures
import contextlib
import threading
from typing import TYPE_CHECKING, Final

from ralph.mcp.websearch.backends.base import WebSearchError
from ralph.timeout_defaults import WEBSEARCH_SDK_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from collections.abc import Callable


DEFAULT_MAX_WORKERS: Final[int] = 4

SDK_DEFAULT_TIMEOUT_SECONDS: Final[float] = WEBSEARCH_SDK_TIMEOUT_SECONDS


def default_sdk_timeout_seconds() -> float:
    return WEBSEARCH_SDK_TIMEOUT_SECONDS


def _default_label(callable_: Callable[[], object]) -> str:
    qualname: object = getattr(callable_, "__qualname__", None)
    name: object = getattr(callable_, "__name__", "websearch")
    if isinstance(qualname, str) and qualname:
        return qualname
    if isinstance(name, str) and name:
        return name
    return "websearch"


class _BoundedSdkCall:
    def __init__(self, max_workers: int = DEFAULT_MAX_WORKERS) -> None:
        self._max_workers = max_workers
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._lock = threading.Lock()

    @property
    def max_workers(self) -> int:
        return self._max_workers

    def _ensure_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        if self._executor is None:
            with self._lock:
                if self._executor is None:
                    self._executor = concurrent.futures.ThreadPoolExecutor(
                        max_workers=self._max_workers,
                        thread_name_prefix="mcp-websearch-sdk",
                    )
        return self._executor

    def submit[T](
        self,
        callable_: Callable[[], T],
        timeout_seconds: float | None,
        *,
        label: str | None = None,
    ) -> T:
        if timeout_seconds is None:
            return callable_()
        executor = self._ensure_executor()
        future = executor.submit(callable_)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError as exc:
            effective_label = label if label is not None else _default_label(callable_)
            raise WebSearchError(f"{effective_label} timed out after {timeout_seconds}s") from exc

    def install_executor(self, executor: concurrent.futures.ThreadPoolExecutor) -> None:
        with self._lock:
            self._executor = executor
            self._max_workers = executor._max_workers

    def reset(self) -> None:
        with self._lock:
            if self._executor is not None:
                self._executor.shutdown(wait=True)
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="mcp-websearch-sdk",
            )

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            executor = self._executor
            self._executor = None
        if executor is not None:
            try:
                executor.shutdown(wait=wait, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=wait)


_default_call = _BoundedSdkCall()

_atexit_registered: bool = False


def _atexit_shutdown() -> None:
    with contextlib.suppress(BaseException):
        _default_call.shutdown(wait=False)


if not _atexit_registered:
    atexit.register(_atexit_shutdown)
    _atexit_registered = True


def with_timeout[T](
    callable_: Callable[[], T],
    timeout_seconds: float | None,
    *,
    label: str | None = None,
    _executor: concurrent.futures.ThreadPoolExecutor | None = None,
) -> T:
    if _executor is not None:
        if timeout_seconds is None:
            return callable_()
        future = _executor.submit(callable_)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError as exc:
            effective_label = label if label is not None else _default_label(callable_)
            raise WebSearchError(f"{effective_label} timed out after {timeout_seconds}s") from exc
    return _default_call.submit(callable_, timeout_seconds, label=label)


def install_default_executor(executor: concurrent.futures.ThreadPoolExecutor) -> None:
    _default_call.install_executor(executor)


def reset_default() -> None:
    _default_call.reset()


def shutdown(wait: bool = True) -> None:
    _default_call.shutdown(wait=wait)


__all__ = [
    "DEFAULT_MAX_WORKERS",
    "install_default_executor",
    "reset_default",
    "shutdown",
    "with_timeout",
]
