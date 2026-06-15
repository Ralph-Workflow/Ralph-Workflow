"""Bounded Pro heartbeat client.

The engine, when running as a Pro subprocess, POSTs a small JSON
heartbeat to ``<base_url>/api/heartbeat`` every ``interval_seconds``
seconds so Pro can monitor liveness. The heartbeat client is a
self-contained class that:

- runs the heartbeat loop in a **daemon thread** so the process can
  always exit even if Pro is hung;
- uses an **explicit bounded ``timeout=``** on every ``httpx`` call so
  the bounded-subprocess audit (``ralph.testing.audit_mcp_timeout``)
  catches any regression;
- treats ``401`` and ``404`` responses as **hard stops** — once the
  heartbeat is rejected as unauthorized or unknown, the client logs a
  warning and stops looping;
- treats every other error (connection refused, timeout, 5xx) as
  **transient** — log at debug level and continue, so a Pro restart or
  brief outage does not crash the pipeline;
- exposes an **idempotent ``stop()``** that only sets a
  ``threading.Event``; it does NOT join the worker because daemon
  threads cannot be meaningfully joined and the process must never
  block on a slow Pro server.

The client does not perform I/O at construction time. ``start()``
launches the daemon thread. ``stop()`` is safe to call multiple times
and from any thread.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


logger = logging.getLogger(__name__)


# HTTP status codes for the Pro heartbeat endpoint. Defined as
# module-level constants so the magic-number check in
# ``_post_once`` does not trigger PLR2004.
_STATUS_UNAUTHORIZED = 401
_STATUS_NOT_FOUND = 404
_STATUS_SERVER_ERROR_THRESHOLD = 500


class _HttpxResponseLike(Protocol):
    """Minimum surface we need from a httpx-like response object."""

    status_code: int


class _HttpxClientLike(Protocol):
    """Minimum surface we need from a httpx-like client."""

    def post(
        self,
        url: str,
        *,
        json: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> _HttpxResponseLike: ...


class _HttpxClientFactory(Protocol):
    def __call__(self) -> _HttpxClientLike: ...


def _default_httpx_client_factory() -> _HttpxClientLike:
    """Return a default ``httpx.Client`` for production use.

    The factory indirection exists solely so tests can substitute a
    fake without monkeypatching the ``httpx`` module. The import is
    deliberately lazy so the pro_support module remains importable
    even when ``httpx`` is not installed (e.g. during lightweight
    test runs).
    """
    import httpx  # noqa: PLC0415 - lazy import to keep pro_support importable without httpx

    # Bound the default client at construction so a slow Pro server
    # can never block the daemon thread at the connection layer; the
    # per-request timeout is still applied via ``timeout=self._timeout``
    # in ``_post_once``.
    return httpx.Client(timeout=5.0)


def _default_clock() -> float:
    return time.monotonic()


class ProHeartbeatClient:
    """Bounded, daemon-threaded heartbeat client for the Pro subprocess contract.

    Constructor parameters are explicit (no module-level mutable state)
    so every test can construct a client with a fake clock and a fake
    httpx factory.
    """

    def __init__(
        self,
        run_id: str,
        token: str,
        base_url: str,
        pid: int,
        *,
        interval_seconds: float = 5.0,
        timeout_seconds: float = 5.0,
        httpx_client_factory: _HttpxClientFactory | None = None,
        clock: Callable[[], float] | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._run_id = run_id
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._pid = pid
        self._interval = float(interval_seconds)
        self._timeout = float(timeout_seconds)
        self._client_factory: _HttpxClientFactory = (
            httpx_client_factory
            if httpx_client_factory is not None
            else _default_httpx_client_factory
        )
        self._clock: Callable[[], float] = clock if clock is not None else _default_clock
        self._metadata: dict[str, object] = dict(metadata) if metadata is not None else {}
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Spawn the daemon worker thread. Idempotent: a second call is a no-op."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        thread = threading.Thread(
            target=self._run_loop,
            name="ralph-pro-heartbeat",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        """Signal the worker to exit on its next loop iteration. Idempotent.

        Deliberately does NOT ``join()``: the worker is daemonic and
        therefore will be torn down with the process; joining a daemon
        thread can block on a slow Pro server which would defeat the
        entire point of the design.
        """
        self._stop_event.set()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _build_payload(self) -> dict[str, object]:
        return {
            "run_id": self._run_id,
            "token": self._token,
            "status": "running",
            "pid": self._pid,
            "metadata": dict(self._metadata),
        }

    def _run_loop(self) -> None:
        next_tick_at = self._clock()
        while not self._stop_event.is_set():
            now = self._clock()
            if now < next_tick_at:
                self._sleep_for(next_tick_at - now)
                continue
            self._post_once()
            next_tick_at = self._clock() + self._interval

    def _sleep_for(self, seconds: float) -> None:
        """Sleep until the stop event fires or ``seconds`` elapses.

        Implemented with ``Event.wait(timeout=...)`` so a ``stop()`` call
        from the main thread can interrupt the sleep immediately rather
        than waiting out the full interval. ``Event.wait`` is bounded by
        its timeout so it cannot wedge the daemon.
        """
        self._stop_event.wait(timeout=seconds)

    def _post_once(self) -> None:
        url = f"{self._base_url}/api/heartbeat"
        payload = self._build_payload()
        try:
            client = self._client_factory()
        except Exception as exc:
            logger.debug("Pro heartbeat client creation failed (transient): %s", exc)
            return
        try:
            try:
                response = client.post(url, json=payload, timeout=self._timeout)
            except Exception as exc:
                logger.debug("Pro heartbeat POST to %s failed (transient): %s", url, exc)
                return
            status_code: int = response.status_code
            if status_code in (_STATUS_UNAUTHORIZED, _STATUS_NOT_FOUND):
                logger.warning(
                    "Pro heartbeat rejected with status %s; stopping heartbeat loop",
                    status_code,
                )
                self._stop_event.set()
                return
            if status_code >= _STATUS_SERVER_ERROR_THRESHOLD:
                logger.debug(
                    "Pro heartbeat transient server error %s; continuing",
                    status_code,
                )
                return
        finally:
            close_method: Callable[[], object] | None = getattr(client, "close", None)
            if callable(close_method):
                try:
                    close_method()
                except Exception:
                    logger.debug("Pro heartbeat client close failed", exc_info=True)


__all__ = ["ProHeartbeatClient"]
