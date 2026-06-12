"""Black-box unit tests for :mod:`ralph.pro_support.marker`."""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING

from ralph.pro_support.heartbeat import ProHeartbeatClient
from ralph.pro_support.marker import (
    read_heartbeat_port,
    read_heartbeat_token,
    read_marker_file,
    read_run_id,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_read_marker_file_returns_none_when_missing(tmp_path: Path) -> None:
    assert read_marker_file(tmp_path) is None


def test_read_marker_file_returns_none_on_invalid_json(tmp_path: Path) -> None:
    marker_dir = tmp_path / ".ralph"
    marker_dir.mkdir()
    (marker_dir / "run.json").write_text("not-json{", encoding="utf-8")
    assert read_marker_file(tmp_path) is None


def test_read_marker_file_returns_none_when_not_a_dict(tmp_path: Path) -> None:
    marker_dir = tmp_path / ".ralph"
    marker_dir.mkdir()
    (marker_dir / "run.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert read_marker_file(tmp_path) is None


def test_read_marker_file_returns_parsed_dict(tmp_path: Path) -> None:
    marker_dir = tmp_path / ".ralph"
    marker_dir.mkdir()
    payload = {"runId": "abc", "port": 7777, "heartbeatToken": "secret"}
    (marker_dir / "run.json").write_text(json.dumps(payload), encoding="utf-8")
    result = read_marker_file(tmp_path)
    assert result == payload


def test_read_marker_file_does_not_create_marker(tmp_path: Path) -> None:
    """``read_marker_file`` MUST NOT create a marker on a missing-file read."""
    marker_path = tmp_path / ".ralph" / "run.json"
    assert not marker_path.exists()
    assert read_marker_file(tmp_path) is None
    assert not marker_path.exists(), (
        "read_marker_file must not create the marker file when it is missing"
    )


def test_read_heartbeat_token_prefers_marker_field(tmp_path: Path) -> None:
    marker_dir = tmp_path / ".ralph"
    marker_dir.mkdir()
    (marker_dir / "heartbeat_token").write_text("sidecar-token\n", encoding="utf-8")
    (marker_dir / "run.json").write_text(
        json.dumps({"heartbeatToken": "marker-token"}), encoding="utf-8"
    )
    assert read_heartbeat_token(tmp_path) == "marker-token"


def test_read_heartbeat_token_falls_back_to_sidecar(tmp_path: Path) -> None:
    marker_dir = tmp_path / ".ralph"
    marker_dir.mkdir()
    (marker_dir / "heartbeat_token").write_text("sidecar-token\n", encoding="utf-8")
    assert read_heartbeat_token(tmp_path) == "sidecar-token"


def test_read_heartbeat_token_returns_none_when_missing(tmp_path: Path) -> None:
    assert read_heartbeat_token(tmp_path) is None


def test_read_heartbeat_token_handles_empty_sidecar(tmp_path: Path) -> None:
    marker_dir = tmp_path / ".ralph"
    marker_dir.mkdir()
    (marker_dir / "heartbeat_token").write_text("   \n", encoding="utf-8")
    assert read_heartbeat_token(tmp_path) is None


def test_read_heartbeat_port_default_is_7432() -> None:
    assert read_heartbeat_port(None) == 7432
    assert read_heartbeat_port({}) == 7432
    assert read_heartbeat_port({"port": 0}) == 7432
    assert read_heartbeat_port({"port": -5}) == 7432


def test_read_heartbeat_port_respects_marker() -> None:
    assert read_heartbeat_port({"port": 7777}) == 7777


def test_read_run_id_returns_string_when_present() -> None:
    assert read_run_id({"runId": "abc"}) == "abc"


def test_read_run_id_returns_none_for_empty_or_missing() -> None:
    assert read_run_id(None) is None
    assert read_run_id({}) is None
    assert read_run_id({"runId": ""}) is None
    assert read_run_id({"runId": 42}) is None


def test_heartbeat_client_does_not_write_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A heartbeat client MUST NOT write to ``<workspace>/.ralph/run.json``.

    This is the read-only invariant the Pro contract depends on. The
    test wires a fake httpx client that records its calls, injects a
    controllable clock, and asserts the marker file's stat and contents
    are byte-for-byte unchanged across two heartbeat ticks.
    """
    marker_dir = tmp_path / ".ralph"
    marker_dir.mkdir()
    original = {"runId": "fixed", "port": 7432, "heartbeatToken": "tok"}
    marker_path = marker_dir / "run.json"
    marker_path.write_text(json.dumps(original), encoding="utf-8")

    before = marker_path.stat()
    before_text = marker_path.read_text(encoding="utf-8")

    tick_log: list[dict[str, object]] = []
    tick_signal = threading.Event()

    class _FakeResponse:
        status_code = 200

    class _FakeClient:
        def post(
            self,
            url: str,
            *,
            json: dict[str, object] | None = None,
            timeout: float | None = None,
        ) -> _FakeResponse:
            tick_log.append({"url": url, "json": json, "timeout": timeout})
            tick_signal.set()
            return _FakeResponse()

    def _factory() -> _FakeClient:
        return _FakeClient()

    now = [0.0]

    def _clock() -> float:
        return now[0]

    client = ProHeartbeatClient(
        run_id="fixed",
        token="tok",
        base_url="http://localhost:7432",
        pid=99999,
        interval_seconds=0.001,
        timeout_seconds=1.0,
        httpx_client_factory=_factory,
        clock=_clock,
    )

    client.start()
    try:
        # Drive the loop with explicit clock advances. The heartbeat
        # worker blocks in ``Event.wait(timeout=interval)`` between
        # posts, releasing the GIL, so the main thread can push the
        # clock forward and the worker can observe the next tick.
        for _ in range(3):
            tick_signal.clear()
            now[0] += 0.001
            assert tick_signal.wait(timeout=2.0), "heartbeat worker missed a tick"
    finally:
        client.stop()

    after = marker_path.stat()
    after_text = marker_path.read_text(encoding="utf-8")
    assert before.st_mtime_ns == after.st_mtime_ns
    assert before_text == after_text
    assert json.loads(after_text) == original, "marker contents must not be mutated"
    # The client should have posted at least once
    assert tick_log, "expected at least one heartbeat tick"
    assert all(entry["url"].endswith("/api/heartbeat") for entry in tick_log)
    assert all(entry["timeout"] == 1.0 for entry in tick_log)


def test_marker_reader_handles_directory_in_place_of_file(tmp_path: Path) -> None:
    """A ``.ralph`` directory without a ``run.json`` returns ``None`` gracefully."""
    (tmp_path / ".ralph").mkdir()
    assert read_marker_file(tmp_path) is None
