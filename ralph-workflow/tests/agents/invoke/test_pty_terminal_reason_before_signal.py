"""Black-box ordering tests: PTY reader records a terminal reason before any signal.

wt-071 / R7: every force-kill-capable PTY termination path must record a
named permitted terminal reason BEFORE sending its first signal, so the
"survived graceful terminate, escalating to force kill" warning can only
follow a recorded reason. These tests observe the ordering on the handle
doubles and fail when a signal precedes its reason record.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.agents.invoke._pty_line_reader import PtyLineReader

if TYPE_CHECKING:
    import pytest


class _OrderHandle:
    """Handle double that records the relative order of reason vs. signal calls."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.pid = 4242

    def record_terminal_reason(self, reason: str) -> None:
        self.events.append(("reason", reason))

    def terminate(self, grace_period_s: float | None = None) -> None:
        self.events.append(("signal", "terminate"))

    def close(self) -> None:
        self.events.append(("signal", "close"))

    def wait(self, timeout: float | None = None) -> None:
        return None

    def poll(self) -> int | None:
        return None


def _first(events: list[tuple[str, str]], kind: str) -> int | None:
    for index, (event_kind, _detail) in enumerate(events):
        if event_kind == kind:
            return index
    return None


def _make_reader(handle: _OrderHandle) -> PtyLineReader:
    reader = object.__new__(PtyLineReader)
    reader._handle = handle
    reader._monitor_stop = threading.Event()
    reader._quota_error = None
    reader._agent_name = "claude"
    reader._lines_event = threading.Event()
    reader._lines_lock = threading.Lock()
    reader._lines_queue: list[str] = []
    reader._input_writer_fd = -1
    reader._input_writer_lock = threading.Lock()
    reader._policy = None
    reader._completion_exit_sent = False
    return reader


def _no_teardown(monkeypatch: pytest.MonkeyPatch, calls: list[int]) -> None:
    def _track(pid: int) -> None:
        calls.append(pid)

    monkeypatch.setattr(
        "ralph.agents.invoke._pty_line_reader.teardown_subtree",
        _track,
    )


def test_on_interrupt_records_reason_before_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_teardown(monkeypatch, [])
    handle = _OrderHandle()
    _make_reader(handle)._on_interrupt()
    reason_at = _first(handle.events, "reason")
    signal_at = _first(handle.events, "signal")
    assert reason_at is not None and signal_at is not None
    assert reason_at < signal_at
    assert handle.events[reason_at] == ("reason", "operator_cancellation")


def test_terminate_for_quota_records_reason_before_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_teardown(monkeypatch, [])
    handle = _OrderHandle()
    reader = _make_reader(handle)
    reader._terminate_for_quota("quota detail")
    reason_at = _first(handle.events, "reason")
    signal_at = _first(handle.events, "signal")
    assert reason_at is not None and signal_at is not None
    assert reason_at < signal_at
    assert handle.events[reason_at] == ("reason", "quota_exhausted")
    assert reader._quota_error is not None


def test_request_interactive_exit_records_reason_before_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_teardown(monkeypatch, [])
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_line_reader._write_pty_input",
        lambda *args, **kwargs: None,
    )
    handle = _OrderHandle()
    _make_reader(handle)._request_interactive_exit()
    reason_at = _first(handle.events, "reason")
    signal_at = _first(handle.events, "signal")
    assert reason_at is not None and signal_at is not None
    assert reason_at < signal_at
    assert handle.events[reason_at] == ("reason", "interactive_completion")


def test_record_terminal_reason_uses_handle_recorder_when_present() -> None:
    reader = object.__new__(PtyLineReader)
    recorder = MagicMock()
    reader._handle = recorder
    PtyLineReader._record_terminal_reason(reader, "quota_exhausted")
    recorder.record_terminal_reason.assert_called_once_with("quota_exhausted")


def test_record_terminal_reason_tolerates_missing_recorder() -> None:
    reader = object.__new__(PtyLineReader)
    reader._handle = object()  # no record_terminal_reason attribute
    PtyLineReader._record_terminal_reason(reader, "quota_exhausted")  # must not raise

