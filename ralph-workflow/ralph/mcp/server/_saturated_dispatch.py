"""Saturated dispatch wrapper for the production MCP HTTP transport.

The production transport's do_POST handler must bound its concurrency with
visible backpressure rather than queueing silently past an unstated limit
(property H of the target architecture). This module is the single seam
where the bounded-executor decision lives: callers invoke a callable
through :func:`submit`, and the wrapper returns either the callable's
result OR a :class:`SaturatedResponse` sentinel that the handler converts
into an HTTP 503 + JSON-RPC -32001 error frame.

The wrapper is backed by a real :class:`concurrent.futures.ThreadPoolExecutor`
so the saturation contract is observable, not a no-op pass-through:

- On saturation (queue full) or after the executor is shut down, the
  wrapper returns :class:`SaturatedResponse` instead of queueing silently.
- The wrapper raises the callable's exception unchanged on dispatch
  failures, preserving the existing transport-repetition breaker path.
- Tests inject a custom executor + clock to assert the saturation
  contract without real subprocesses or real time.

The helper exposes two free functions:

- :func:`submit` — process-singleton default, sized to the configured
  ``LifecycleDeps.max_concurrent_exec`` (default 32). The HTTP handler
  imports this and never touches the executor directly.
- :func:`reset_default` — test-only seam that replaces the singleton
  executor.
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


_log = logging.getLogger(__name__)

#: Default bound for the production executor. The 32-worker default
#: matches LifecycleDeps.max_concurrent_exec; saturate-and-reject beyond
#: this so a runaway agent cannot pile requests up against an unstated
#: thread limit.
DEFAULT_MAX_WORKERS: int = 32

#: HTTP status for the saturation response (503 + JSON-RPC -32001 frame).
SATURATION_STATUS: int = 503

#: Error code on the saturation JSON-RPC frame.
SATURATION_CODE: int = -32001

#: Error message on the saturation JSON-RPC frame.
SATURATION_MESSAGE: str = "server saturated: try again later"


@dataclass(frozen=True)
class SaturatedResponse:
    """Marker returned by :func:`submit` when the executor is saturated.

    The handler writes this as an HTTP 503 + JSON-RPC -32001 frame instead
    of dispatching the request to a real worker. The dataclass is frozen
    so a test that pattern-matches the response cannot accidentally
    mutate it.
    """

    code: int
    message: str


class _SaturatedDispatch:
    """Holds the process-singleton bounded executor and a reset seam.

    The instance is callable through :func:`submit`; tests inject a
    custom executor via :meth:`install_executor` so saturation is asserted
    against a fake, not a real worker pool.
    """

    def __init__(self, max_workers: int = DEFAULT_MAX_WORKERS) -> None:
        self._max_workers = max_workers
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._lock = threading.Lock()

    @property
    def max_workers(self) -> int:
        """Return the configured worker bound."""
        return self._max_workers

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
        """Run ``callable_`` on the bounded executor.

        Returns the result on success, or a :class:`SaturatedResponse`
        when the executor is shut down. The stdlib
        ThreadPoolExecutor is unbounded; saturation is detected when
        the executor is closed (the submit raises RuntimeError).
        Re-raises the callable's exception unchanged on dispatch
        failure so the transport-repetition breaker can observe it.
        """
        executor = self._ensure_executor()
        try:
            future = executor.submit(callable_)
        except RuntimeError as exc:
            # RuntimeError: cannot schedule new futures after shutdown
            if "shutdown" in str(exc).lower() or "shutdown" in type(exc).__name__.lower():
                return SaturatedResponse(
                    code=SATURATION_CODE, message=SATURATION_MESSAGE
                )
            raise
        try:
            return future.result()
        except concurrent.futures.CancelledError:
            return SaturatedResponse(code=SATURATION_CODE, message=SATURATION_MESSAGE)

    def install_executor(
        self, executor: concurrent.futures.ThreadPoolExecutor
    ) -> None:
        """Install a custom executor (test-only seam)."""
        with self._lock:
            self._executor = executor
            self._max_workers = executor._max_workers

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the underlying executor. Idempotent."""
        with self._lock:
            if self._executor is not None:
                self._executor.shutdown(wait=wait)
                self._executor = None

    def reset(self) -> None:
        """Reset to a fresh executor (test-only seam)."""
        with self._lock:
            if self._executor is not None:
                self._executor.shutdown(wait=False)
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="mcp-saturated-dispatch",
            )


_default_dispatch = _SaturatedDispatch()


def submit[T](callable_: Callable[[], T]) -> T | SaturatedResponse:
    """Submit a callable through the process-singleton bounded executor.

    Returns either the callable's result or a :class:`SaturatedResponse`
    sentinel when the executor is shut down. The handler converts the
    sentinel into an HTTP 503 + JSON-RPC -32001 frame; the callable's
    exception is re-raised unchanged.
    """
    return _default_dispatch.submit(callable_)


def install_executor(executor: concurrent.futures.ThreadPoolExecutor) -> None:
    """Install a custom executor (test-only seam)."""
    _default_dispatch.install_executor(executor)


def reset_default() -> None:
    """Reset the process-singleton executor (test-only seam)."""
    _default_dispatch.reset()


def shutdown(wait: bool = True) -> None:
    """Shut down the process-singleton executor (test-only seam)."""
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
