from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Protocol

from ralph.pro_support.env import is_pro_mode
from ralph.pro_support.heartbeat import ProHeartbeatClient
from ralph.pro_support.marker import read_heartbeat_token, read_marker_file

if TYPE_CHECKING:
    from pathlib import Path


logger = logging.getLogger(__name__)


_HEARTBEAT_PORT_DEFAULT = 7432


class _HeartbeatFactory(Protocol):
    def __call__(self, payload: dict[str, object]) -> ProHeartbeatClient | None: ...


class _MarkerLoader(Protocol):
    def __call__(self) -> dict[str, object] | None: ...


class _Sleeper(Protocol):
    def __call__(self, seconds: float) -> None: ...


class _Clock(Protocol):
    def __call__(self) -> float: ...


def _default_clock() -> float:
    return time.monotonic()


def _default_sleeper_factory(stop_event: threading.Event) -> _Sleeper:
    def _sleep(seconds: float) -> None:
        stop_event.wait(timeout=seconds)

    return _sleep


def _default_marker_loader_factory(workspace_root: Path) -> _MarkerLoader:
    def _loader() -> dict[str, object] | None:
        marker = read_marker_file(workspace_root)
        if marker is None:
            return None
        run_id_obj = marker.get("runId")
        if not isinstance(run_id_obj, str) or not run_id_obj:
            return None
        token = read_heartbeat_token(workspace_root)
        if token is None:
            return None
        port_obj = marker.get("port")
        port = port_obj if isinstance(port_obj, int) and port_obj > 0 else _HEARTBEAT_PORT_DEFAULT
        return {"run_id": run_id_obj, "token": token, "port": port}

    return _loader


def _default_heartbeat_factory() -> _HeartbeatFactory:
    def _factory(payload: dict[str, object]) -> ProHeartbeatClient | None:
        if not is_pro_mode():
            return None
        run_id_obj = payload.get("run_id")
        token_obj = payload.get("token")
        port_obj = payload.get("port")
        if not isinstance(run_id_obj, str) or not isinstance(token_obj, str):
            return None
        if not isinstance(port_obj, int):
            return None
        base_url = f"http://localhost:{port_obj}"
        client = ProHeartbeatClient(
            run_id=run_id_obj,
            token=token_obj,
            base_url=base_url,
            pid=os.getpid(),
        )
        client.start()
        logger.info("Pro heartbeat started: run_id=%s base_url=%s", run_id_obj, base_url)
        return client

    return _factory


class ProMarkerWatcher:
    def __init__(
        self,
        *,
        workspace_root: Path | None = None,
        poll_interval_seconds: float = 2.0,
        marker_loader: _MarkerLoader | None = None,
        heartbeat_factory: _HeartbeatFactory | None = None,
        clock: _Clock | None = None,
        sleeper: _Sleeper | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._poll_interval = float(poll_interval_seconds)
        self._stop_event = threading.Event()
        self._clock: _Clock = clock if clock is not None else _default_clock
        self._sleeper: _Sleeper = (
            sleeper if sleeper is not None else _default_sleeper_factory(self._stop_event)
        )
        if marker_loader is not None:
            self._loader: _MarkerLoader = marker_loader
        elif workspace_root is not None:
            self._loader = _default_marker_loader_factory(workspace_root)
        else:
            raise ValueError("ProMarkerWatcher requires either workspace_root or marker_loader")
        self._heartbeat_factory: _HeartbeatFactory = (
            heartbeat_factory if heartbeat_factory is not None else _default_heartbeat_factory()
        )
        self._thread: threading.Thread | None = None
        self._heartbeat_started = False
        self._heartbeat_client: ProHeartbeatClient | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        thread = threading.Thread(
            target=self._run_loop,
            name="ralph-pro-marker-watcher",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._heartbeat_client is not None:
            with suppress(Exception):
                self._heartbeat_client.stop()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_heartbeat_started(self) -> bool:
        return self._heartbeat_started

    @property
    def heartbeat_client(self) -> ProHeartbeatClient | None:
        return self._heartbeat_client

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                payload = self._loader()
            except Exception as exc:
                logger.debug("Pro marker loader raised: %s", exc)
                payload = None
            if payload is not None:
                logger.debug("Pro marker appeared; attempting heartbeat adoption")
                client = self._heartbeat_factory(payload)
                self._heartbeat_client = client
                self._heartbeat_started = True
                return
            logger.debug("Pro marker not yet present; sleeping")
            self._sleeper(self._poll_interval)


__all__ = ["ProMarkerWatcher"]
