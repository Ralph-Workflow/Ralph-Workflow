"""Bounded-call helper for third-party-SDK-backed websearch backends.

The DDGS, Exa, and Tavily backends wrap Python SDKs that manage their own
HTTP client lifecycle. A hung SDK would otherwise block the dispatch
worker for the full MCP client request timeout (the OpenCode client uses
``EXEC_MAX_TIMEOUT_MS + 30s`` ~= 330s), and the only way to bound that
block is to run the SDK call on a worker pool with ``Future.result(timeout=...)``.

This module exposes a single public function ``with_timeout`` and two test
seams ``install_default_executor`` / ``reset_default``. The function:

- If ``timeout_seconds is None``, returns ``callable_()`` directly (escape
  hatch for tests; not used in production paths).
- If ``_executor is None``, uses the module-level lazy singleton
  ``_default_executor`` (a :class:`concurrent.futures.ThreadPoolExecutor`
  with ``max_workers=4`` and ``thread_name_prefix='mcp-websearch-sdk'``).
- Submits the callable to the executor and calls
  ``future.result(timeout=timeout_seconds)``.
- On :class:`concurrent.futures.TimeoutError`, raises
  :class:`WebSearchError` with the timeout label so the dispatch worker
  is released within the configured budget.
- On any other exception from the future, re-raises it unchanged so
  callers see the original error (exception type, message, and
  traceback preserved).
- Returns the callable's result on success.

The helper uses ``Future.result(timeout=...)`` (NOT ``signal.alarm``, NOT
polling) so the timeout is fail-closed even if the inner callable swallows
signals. The thread pool is sized to 4 workers because the 3 SDK backends
are I/O-bound and we do not want a runaway agent to spawn hundreds of
SDK threads.

The :func:`install_default_executor` test seam replaces the lazy singleton
with a custom executor (used by tests to inject a fake executor). The
:func:`reset_default` test seam recreates the lazy singleton (used by
tests to restore the singleton after each test). Both seams mirror the
pattern at :mod:`ralph.mcp.server._saturated_dispatch`.
"""

from __future__ import annotations

import concurrent.futures
import threading
from typing import TYPE_CHECKING, Final

from ralph.mcp.websearch.backends.base import WebSearchError
from ralph.timeout_defaults import WEBSEARCH_SDK_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from collections.abc import Callable


#: Default worker count for the lazy singleton executor. Four workers
#: cover the three SDK backends with one slot of headroom; a runaway
#: agent cannot pile more than four SDK calls onto the pool at once.
DEFAULT_MAX_WORKERS: Final[int] = 4

#: Re-export of the central SDK timeout default so SDK backend modules can
#: import the constant via ``ralph.mcp.websearch._bounded_sdk_call`` (the
#: bounded-call helper) rather than via ``ralph.timeout_defaults`` directly.
#: The drift-detection audit (``tests/test_audit_websearch_backend_centralized_timeout.py``)
#: enforces that SDK backends do NOT import the constant from
#: ``ralph.timeout_defaults``; they must go through this helper so the source
#: of truth remains a single module.
SDK_DEFAULT_TIMEOUT_SECONDS: Final[float] = WEBSEARCH_SDK_TIMEOUT_SECONDS


def default_sdk_timeout_seconds() -> float:
    """Return the central SDK timeout default.

    SDK backends (``DdgsBackend``, ``ExaBackend``, ``TavilyBackend``) call
    this function when their constructor-level ``timeout_seconds`` is
    ``None`` to resolve the per-call effective timeout. The function
    preserves a single import site for the constant so a future change to
    the default (e.g. moving it to a config field) is a one-line edit.
    """
    return WEBSEARCH_SDK_TIMEOUT_SECONDS


def _default_label(callable_: Callable[[], object]) -> str:
    """Return a short label identifying the callable for the timeout error."""
    qualname: object = getattr(callable_, "__qualname__", None)
    name: object = getattr(callable_, "__name__", "websearch")
    if isinstance(qualname, str) and qualname:
        return qualname
    if isinstance(name, str) and name:
        return name
    return "websearch"


class _BoundedSdkCall:
    """Holds the process-singleton executor and a reset seam.

    The instance is callable through :func:`with_timeout`; tests inject a
    custom executor via :meth:`install_executor` so timeouts are asserted
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
        """Run ``callable_`` on the bounded executor with a hard timeout.

        Returns the callable's result on success. Raises
        :class:`WebSearchError` on timeout. Re-raises the callable's
        exception unchanged on dispatch failure so the caller sees the
        original error.
        """
        if timeout_seconds is None:
            return callable_()
        executor = self._ensure_executor()
        future = executor.submit(callable_)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError as exc:
            effective_label = label if label is not None else _default_label(callable_)
            raise WebSearchError(
                f"{effective_label} timed out after {timeout_seconds}s"
            ) from exc

    def install_executor(self, executor: concurrent.futures.ThreadPoolExecutor) -> None:
        """Install a custom executor (test-only seam)."""
        with self._lock:
            self._executor = executor
            self._max_workers = executor._max_workers

    def reset(self) -> None:
        """Reset to a fresh executor (test-only seam)."""
        with self._lock:
            if self._executor is not None:
                self._executor.shutdown(wait=False)
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="mcp-websearch-sdk",
            )

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the underlying executor. Idempotent."""
        with self._lock:
            if self._executor is not None:
                self._executor.shutdown(wait=wait)
                self._executor = None


_default_call = _BoundedSdkCall()


def with_timeout[T](
    callable_: Callable[[], T],
    timeout_seconds: float | None,
    *,
    label: str | None = None,
    _executor: concurrent.futures.ThreadPoolExecutor | None = None,
) -> T:
    """Run ``callable_`` on a worker pool with a hard timeout.

    Args:
        callable_: Zero-argument callable to run. If it raises, the
            exception propagates unchanged (no wrapping). If it returns,
            the value is returned.
        timeout_seconds: Per-call timeout in seconds. ``None`` means
            "run inline" (escape hatch for tests; production paths
            always pass a float).
        label: Optional human-readable label for the timeout error
            message. Defaults to ``callable_.__qualname__``.
        _executor: Optional pre-built executor (test-only seam).
            Production paths use the lazy singleton.

    Returns:
        The callable's return value on success.

    Raises:
        WebSearchError: When ``timeout_seconds`` elapses before the
            callable returns. The error message includes the label and
            the timeout value.
        BaseException: The callable's own exception, re-raised unchanged.
    """
    if _executor is not None:
        if timeout_seconds is None:
            return callable_()
        future = _executor.submit(callable_)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError as exc:
            effective_label = label if label is not None else _default_label(callable_)
            raise WebSearchError(
                f"{effective_label} timed out after {timeout_seconds}s"
            ) from exc
    return _default_call.submit(callable_, timeout_seconds, label=label)


def install_default_executor(executor: concurrent.futures.ThreadPoolExecutor) -> None:
    """Install a custom executor on the singleton (test-only seam)."""
    _default_call.install_executor(executor)


def reset_default() -> None:
    """Reset the process-singleton executor (test-only seam)."""
    _default_call.reset()


def shutdown(wait: bool = True) -> None:
    """Shut down the process-singleton executor (test-only seam)."""
    _default_call.shutdown(wait=wait)


__all__ = [
    "DEFAULT_MAX_WORKERS",
    "install_default_executor",
    "reset_default",
    "shutdown",
    "with_timeout",
]
