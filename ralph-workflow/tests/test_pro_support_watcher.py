"""Black-box unit tests for :mod:`ralph.pro_support.watcher`."""

from __future__ import annotations

import io
import json
import re
import threading
from typing import TYPE_CHECKING

import pytest
from loguru import logger as loguru_logger

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

from ralph.pro_support.marker import read_heartbeat_token, read_marker_file
from ralph.pro_support.watcher import ProMarkerWatcher

_BARE_JSON_LINE = re.compile(r"^\s*[\{\[].*[\}\]]\s*$")


def _wait_for_event(
    event: threading.Event, timeout: float = 2.0, *, label: str = "event"
) -> None:
    assert event.wait(timeout=timeout), f"timed out waiting for {label}"


def _is_adopted(watcher: ProMarkerWatcher) -> bool:
    return bool(watcher.is_heartbeat_started)


def _make_noop_sleeper() -> tuple[Callable[[float], None], list[float]]:
    sleeps: list[float] = []

    def _sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    return _sleeper, sleeps


def test_watcher_polls_then_adopts_when_marker_appears() -> None:
    factory_calls: list[dict[str, object]] = []
    load_results: list[dict[str, object] | None] = [None, None, None]
    load_results.append({"run_id": "r-1", "token": "t-1", "port": 7432})
    load_index = 0
    load_lock = threading.Lock()
    adoption_event = threading.Event()

    def _loader() -> dict[str, object] | None:
        nonlocal load_index
        with load_lock:
            result = load_results[load_index] if load_index < len(load_results) else None
            load_index += 1
        return result

    def _factory(payload: dict[str, object]) -> object:
        factory_calls.append(payload)
        adoption_event.set()
        return object()

    _sleeper, _sleeps = _make_noop_sleeper()

    watcher = ProMarkerWatcher(
        sleeper=_sleeper,
        marker_loader=_loader,
        heartbeat_factory=_factory,
        poll_interval_seconds=10.0,
    )
    watcher.start()
    try:
        _wait_for_event(adoption_event, timeout=2.0, label="watcher adoption")
    finally:
        watcher.stop()

    assert len(factory_calls) == 1
    assert factory_calls[0] == {"run_id": "r-1", "token": "t-1", "port": 7432}
    assert watcher.heartbeat_client is not None
    assert watcher.is_heartbeat_started


def test_watcher_with_marker_at_construction_adopts_immediately() -> None:
    factory_calls: list[dict[str, object]] = []
    adoption_event = threading.Event()

    def _loader() -> dict[str, object] | None:
        return {"run_id": "r-x", "token": "t-x", "port": 7777}

    def _factory(payload: dict[str, object]) -> object:
        factory_calls.append(payload)
        adoption_event.set()
        return object()

    _sleeper, _sleeps = _make_noop_sleeper()

    watcher = ProMarkerWatcher(
        sleeper=_sleeper,
        marker_loader=_loader,
        heartbeat_factory=_factory,
        poll_interval_seconds=10.0,
    )
    watcher.start()
    try:
        _wait_for_event(adoption_event, timeout=2.0, label="watcher adoption")
    finally:
        watcher.stop()

    assert len(factory_calls) == 1
    assert factory_calls[0] == {"run_id": "r-x", "token": "t-x", "port": 7777}


def test_watcher_never_calls_heartbeat_factory_when_marker_never_appears() -> None:
    factory_calls: list[dict[str, object]] = []
    load_calls = 0
    load_lock = threading.Lock()
    slept_event = threading.Event()

    def _loader() -> dict[str, object] | None:
        nonlocal load_calls
        with load_lock:
            load_calls += 1
        return None

    def _factory(payload: dict[str, object]) -> object:
        factory_calls.append(payload)
        return object()

    def _sleeper(seconds: float) -> None:
        slept_event.set()

    watcher = ProMarkerWatcher(
        sleeper=_sleeper,
        marker_loader=_loader,
        heartbeat_factory=_factory,
        poll_interval_seconds=0.001,
    )
    watcher.start()
    try:
        _wait_for_event(slept_event, timeout=2.0, label="sleeper entry")
    finally:
        watcher.stop()

    assert not watcher.is_heartbeat_started
    assert factory_calls == []
    assert load_calls >= 1


def test_watcher_stop_interrupts_sleep_within_50ms() -> None:
    """Default sleeper uses ``Event.wait``; ``stop()`` must interrupt promptly.

    The daemon runs in Event.wait(timeout=10.0). Calling stop() from the main
    thread must wake the daemon within 50ms (a real responsiveness assertion;
    see ``_WALL_CLOCK_ALLOWLIST`` justification for ``test_pro_support_watcher``).
    """

    def _loader() -> dict[str, object] | None:
        return None

    watcher = ProMarkerWatcher(
        marker_loader=_loader,
        heartbeat_factory=lambda _p: object(),
        poll_interval_seconds=10.0,
    )
    watcher.start()
    threading.Event().wait(timeout=0.05)
    watcher.stop()
    watcher._thread.join(timeout=0.05)
    assert watcher._thread is None or not watcher._thread.is_alive(), (
        "daemon did not exit within 50ms of stop()"
    )


def test_watcher_rejects_non_positive_poll_interval() -> None:
    def _loader() -> dict[str, object] | None:
        return None

    with pytest.raises(ValueError):
        ProMarkerWatcher(
            marker_loader=_loader,
            heartbeat_factory=lambda _p: object(),
            poll_interval_seconds=0,
        )
    with pytest.raises(ValueError):
        ProMarkerWatcher(
            marker_loader=_loader,
            heartbeat_factory=lambda _p: object(),
            poll_interval_seconds=-1.0,
        )


def test_watcher_does_not_write_to_marker_directory(tmp_path: Path) -> None:
    """The watcher (with the real marker reader) must NEVER write to <workspace>/.ralph/."""
    marker_dir = tmp_path / ".ralph"
    marker_dir.mkdir()
    payload = {"runId": "abc", "port": 7432, "heartbeatToken": "tok"}
    marker_path = marker_dir / "run.json"
    marker_path.write_text(json.dumps(payload), encoding="utf-8")
    before_mtime_ns = marker_path.stat().st_mtime_ns
    before_bytes = marker_path.read_bytes()
    adoption_event = threading.Event()

    def _loader() -> dict[str, object] | None:
        marker = read_marker_file(tmp_path)
        if marker is None:
            return None
        token = read_heartbeat_token(tmp_path)
        run_id_obj = marker.get("runId")
        run_id = run_id_obj if isinstance(run_id_obj, str) and run_id_obj else None
        port_obj = marker.get("port")
        port = port_obj if isinstance(port_obj, int) and port_obj > 0 else 7432
        if run_id is None or token is None:
            return None
        return {"run_id": run_id, "token": token, "port": port}

    def _factory(payload: dict[str, object]) -> object:
        adoption_event.set()
        return object()

    _sleeper, _sleeps = _make_noop_sleeper()

    watcher = ProMarkerWatcher(
        sleeper=_sleeper,
        marker_loader=_loader,
        heartbeat_factory=_factory,
        poll_interval_seconds=10.0,
    )
    watcher.start()
    try:
        _wait_for_event(adoption_event, timeout=2.0, label="watcher adoption")
    finally:
        watcher.stop()

    assert marker_path.exists()
    assert marker_path.stat().st_mtime_ns == before_mtime_ns
    assert marker_path.read_bytes() == before_bytes


def test_watcher_does_not_emit_bare_json_log_line() -> None:
    captured = io.StringIO()
    sink_id = loguru_logger.add(captured, format="{message}", level="DEBUG")
    slept_event = threading.Event()
    try:

        def _loader() -> dict[str, object] | None:
            return None

        def _sleeper(seconds: float) -> None:
            slept_event.set()

        watcher = ProMarkerWatcher(
            sleeper=_sleeper,
            marker_loader=_loader,
            heartbeat_factory=lambda _p: object(),
            poll_interval_seconds=0.001,
        )
        watcher.start()
        try:
            _wait_for_event(slept_event, timeout=2.0, label="sleeper entry")
            assert not watcher.is_heartbeat_started
        finally:
            watcher.stop()
    finally:
        loguru_logger.remove(sink_id)

    text = captured.getvalue()
    for line in text.splitlines():
        assert not _BARE_JSON_LINE.match(line), f"bare JSON log line: {line!r}"
