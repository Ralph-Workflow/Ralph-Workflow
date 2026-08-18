"""Regression coverage for graceful interactive PTY teardown (S-6).

The pre-fix bug: ``PtyLineReader._request_interactive_exit`` sent SIGTERM
immediately after typing ``/exit``, so a completed Claude session that was
still flushing output was force-killed with
"survived graceful terminate, escalating to force kill".

These tests exercise the helper via a hand-crafted ``PtyLineReader`` so no
real subprocess, PTY, sleep, or network is needed.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke._pty_line_reader import (
    _DEFAULT_INTERACTIVE_EXIT_GRACE_SECONDS,
    PtyLineReader,
)
from ralph.agents.invoke._session import TURN_BOUNDARY_MARKER


class _FakeExitedHandle:
    """A process handle that has already exited naturally."""

    def __init__(self) -> None:
        self.terminate_calls: list[Any] = []
        self.wait_calls: list[float | None] = []
        self.pid = 12345

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        return 0

    def poll(self) -> int:
        return 0

    def terminate(self, *, grace_period_s: float | None = None) -> None:
        self.terminate_calls.append(grace_period_s)


class _FakeLiveHandle:
    """A process handle that ignores ``/exit`` and must be terminated."""

    def __init__(self) -> None:
        self.terminate_calls: list[Any] = []
        self.wait_calls: list[float | None] = []
        self.pid = 12345

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        raise TimeoutError

    def poll(self) -> None:
        return None

    def terminate(self, *, grace_period_s: float | None = None) -> None:
        self.terminate_calls.append(grace_period_s)


def _make_reader(handle: object) -> PtyLineReader:
    reader = PtyLineReader.__new__(PtyLineReader)
    reader._completion_exit_sent = False
    reader._lines_queue: list[str] = []
    reader._lines_lock = threading.Lock()
    reader._lines_event = threading.Event()
    reader._monitor_stop = threading.Event()
    reader._input_writer_fd = -1
    reader._input_writer_lock = threading.Lock()
    reader._handle = handle
    reader._policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=10.0,
        parent_exit_grace_seconds=3.0,
    )
    return reader


@pytest.mark.timeout_seconds(3)
def test_request_interactive_exit_waits_for_natural_exit_before_terminate() -> None:
    """A handle that already exited must not be terminated after ``/exit``."""
    handle = _FakeExitedHandle()
    reader = _make_reader(handle)

    reader._request_interactive_exit()

    assert reader._completion_exit_sent is True
    assert TURN_BOUNDARY_MARKER + "\n" in reader._lines_queue
    assert len(handle.wait_calls) == 1
    assert handle.wait_calls[0] == pytest.approx(3.0, abs=0.1)
    assert handle.terminate_calls == []


@pytest.mark.timeout_seconds(3)
def test_request_interactive_exit_escalates_when_handle_ignores_exit() -> None:
    """A handle that ignores ``/exit`` still reaches terminate after the grace."""
    handle = _FakeLiveHandle()
    reader = _make_reader(handle)

    reader._request_interactive_exit()

    assert len(handle.wait_calls) == 1
    assert handle.terminate_calls == [0.5]


@pytest.mark.timeout_seconds(3)
def test_request_interactive_exit_uses_default_grace_when_policy_missing() -> None:
    """Without a policy, the helper uses the module default grace."""
    handle = _FakeExitedHandle()
    reader = _make_reader(handle)
    reader._policy = None

    reader._request_interactive_exit()

    assert handle.wait_calls == [_DEFAULT_INTERACTIVE_EXIT_GRACE_SECONDS]
