from __future__ import annotations

import atexit
import concurrent.futures
import contextlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ralph.timeout_defaults import MCP_DISPATCH_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from collections.abc import Callable


_log = logging.getLogger(__name__)

DEFAULT_MAX_WORKERS: int = 32

_SATURATION_WARNING_INTERVAL_SECONDS: Final[float] = 1.0

_DEFAULT_DISPATCH_TIMEOUT_SECONDS: Final[float] = MCP_DISPATCH_TIMEOUT_SECONDS

SATURATION_STATUS: int = 503

SATURATION_CODE: int = -32001

SATURATION_MESSAGE: str = "server saturated: try again later"


@dataclass(frozen=True)
class SaturatedResponse:
    code: int
    message: str


class _SaturatedDispatch:
    def __init__(
        self,
        max_workers: int = DEFAULT_MAX_WORKERS,
        dispatch_timeout_seconds: float | None = _DEFAULT_DISPATCH_TIMEOUT_SECONDS,
    ) -> None:
        self._max_workers = max_workers
        self._dispatch_timeout_seconds = dispatch_timeout_seconds
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._lock = threading.Lock()
        self._inflight: int = 0
        self._generation: int = 0
        self._last_saturation_warning_at: float | None = None

    @property
    def max_workers(self) -> int:
        return self._max_workers

    @property
    def dispatch_timeout_seconds(self) -> float | None:
        return self._dispatch_timeout_seconds

    def _release_slot(self, generation: int) -> None:
        with self._lock:
            if generation == self._generation:
                self._inflight = max(0, self._inflight - 1)

    def _ensure_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        if self._executor is None:
            with self._lock:
                if self._executor is None:
                    self._executor = concurrent.futures.ThreadPoolExecutor(
                        max_workers=self._max_workers,
                        thread_name_prefix="mcp-saturated-dispatch",
                    )
        return self._executor

    def submit[T](self, callable_: Callable[[], T]) -> T | SaturatedResponse:
        executor = self._ensure_executor()
        with self._lock:
            if self._inflight >= self._max_workers:
                now = time.monotonic()
                if (
                    self._last_saturation_warning_at is None
                    or now - self._last_saturation_warning_at
                    >= _SATURATION_WARNING_INTERVAL_SECONDS
                ):
                    _log.warning("MCP dispatch saturated at max_workers=%s", self._max_workers)
                    self._last_saturation_warning_at = now
                return SaturatedResponse(code=SATURATION_CODE, message=SATURATION_MESSAGE)
            self._inflight += 1
            generation = self._generation

        def _tracked() -> T:
            try:
                return callable_()
            finally:
                self._release_slot(generation)

        try:
            future = executor.submit(_tracked)
        except RuntimeError as exc:
            self._release_slot(generation)
            if "shutdown" in str(exc).lower() or "shutdown" in type(exc).__name__.lower():
                return SaturatedResponse(code=SATURATION_CODE, message=SATURATION_MESSAGE)
            raise
        try:
            if self._dispatch_timeout_seconds is None:
                future.cancel()
                return SaturatedResponse(code=SATURATION_CODE, message=SATURATION_MESSAGE)
            return future.result(timeout=self._dispatch_timeout_seconds)
        except concurrent.futures.TimeoutError:
            future.cancel()
            _log.warning("MCP dispatch timed out after %s seconds", self._dispatch_timeout_seconds)
            return SaturatedResponse(code=SATURATION_CODE, message=SATURATION_MESSAGE)
        except concurrent.futures.CancelledError:
            return SaturatedResponse(code=SATURATION_CODE, message=SATURATION_MESSAGE)

    def install_executor(self, executor: concurrent.futures.ThreadPoolExecutor) -> None:
        with self._lock:
            self._executor = executor
            self._max_workers = executor._max_workers
            self._inflight = 0
            self._generation += 1

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            executor = self._executor
            self._executor = None
            self._inflight = 0
            self._generation += 1
        if executor is not None:
            try:
                executor.shutdown(wait=wait, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=wait)

    def reset(self) -> None:
        with self._lock:
            previous_executor = self._executor
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="mcp-saturated-dispatch",
            )
            self._inflight = 0
            self._generation += 1
        if previous_executor is not None:
            previous_executor.shutdown(wait=True)


_default_dispatch = _SaturatedDispatch()

_atexit_registered: bool = False


def _atexit_shutdown() -> None:
    with contextlib.suppress(BaseException):
        _default_dispatch.shutdown(wait=False)


if not _atexit_registered:
    atexit.register(_atexit_shutdown)
    _atexit_registered = True


def submit[T](callable_: Callable[[], T]) -> T | SaturatedResponse:
    return _default_dispatch.submit(callable_)


def install_executor(executor: concurrent.futures.ThreadPoolExecutor) -> None:
    _default_dispatch.install_executor(executor)


def reset_default() -> None:
    _default_dispatch.reset()


def shutdown(wait: bool = True) -> None:
    _default_dispatch.shutdown(wait=wait)


__all__ = [
    "DEFAULT_MAX_WORKERS",
    "SATURATION_CODE",
    "SATURATION_MESSAGE",
    "SATURATION_STATUS",
    "SaturatedResponse",
    "install_executor",
    "reset_default",
    "shutdown",
    "submit",
]
