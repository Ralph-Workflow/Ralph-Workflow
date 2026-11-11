
from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


logger = logging.getLogger(__name__)


_STATUS_UNAUTHORIZED = 401
_STATUS_NOT_FOUND = 404
_STATUS_SERVER_ERROR_THRESHOLD = 500


class _HttpxResponseLike(Protocol):

    status_code: int


class _HttpxClientLike(Protocol):

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
    import httpx

    return httpx.Client(timeout=5.0)


def _default_clock() -> float:
    return time.monotonic()


class ProHeartbeatClient:

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
            next_tick_at += self._interval

    def _sleep_for(self, seconds: float) -> None:
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
