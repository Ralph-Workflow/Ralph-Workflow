from __future__ import annotations

import sys
import threading
from typing import Any

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke._pty_line_reader import (
    _DEFAULT_INTERACTIVE_EXIT_GRACE_SECONDS,
    _MAX_INTERACTIVE_EXIT_GRACE_SECONDS,
    PtyLineReader,
)
from ralph.agents.invoke.attempt_supervision import attempt_supervisor

_MODULE = sys.modules[PtyLineReader.__module__]
_SUPERVISOR_MODULE = sys.modules[attempt_supervisor.__name__]


class _Handle:
    def __init__(self, *, exited: bool, calls: list[str]) -> None:
        self._exited = exited
        self.calls = calls
        self.wait_timeouts: list[float | None] = []
        self.terminate_calls: list[float | None] = []
        self.pid = 12345

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        self.calls.append(f"wait:{timeout}")
        if not self._exited:
            raise TimeoutError
        return 0

    def poll(self) -> int | None:
        self.calls.append("poll")
        return 0 if self._exited else None

    def terminate(self, *, grace_period_s: float | None = None, **_kwargs: object) -> None:
        self.terminate_calls.append(grace_period_s)
        self.calls.append(f"terminate:{grace_period_s}")


def _reader(handle: _Handle, grace: float | None) -> PtyLineReader:
    reader = PtyLineReader.__new__(PtyLineReader)
    reader._completion_exit_sent = False
    reader._lines_queue = []
    reader._lines_lock = threading.Lock()
    reader._lines_event = threading.Event()
    reader._monitor_stop = threading.Event()
    reader._input_writer_fd = -1
    reader._input_writer_lock = threading.Lock()
    reader._handle = handle
    reader._policy = (
        None
        if grace is None
        else TimeoutPolicy(
            idle_timeout_seconds=60.0,
            no_output_at_start_seconds=10.0,
            parent_exit_grace_seconds=grace,
        )
    )
    return reader


def test_exit_input_precedes_capped_grace_then_termination(monkeypatch: Any) -> None:
    calls: list[str] = []
    handle = _Handle(exited=False, calls=calls)
    reader = _reader(handle, _MAX_INTERACTIVE_EXIT_GRACE_SECONDS + 1.0)
    monkeypatch.setattr(
        _MODULE,
        "_write_pty_input",
        lambda *_args, **_kwargs: calls.append("write:/exit"),
    )
    monkeypatch.setattr(
        _SUPERVISOR_MODULE,
        "teardown_subtree",
        lambda pid, *, issuer: calls.append(f"teardown:{pid}:{issuer}"),
    )

    reader._request_interactive_exit()

    assert handle.wait_timeouts == [_MAX_INTERACTIVE_EXIT_GRACE_SECONDS]
    assert handle.terminate_calls == [0.5]
    assert calls == [
        "write:/exit",
        f"wait:{_MAX_INTERACTIVE_EXIT_GRACE_SECONDS}",
        "poll",
        "terminate:0.5",
        "teardown:12345:invoke_teardown",
    ]


def test_natural_exit_uses_default_grace_without_termination(monkeypatch: Any) -> None:
    calls: list[str] = []
    handle = _Handle(exited=True, calls=calls)
    reader = _reader(handle, None)
    monkeypatch.setattr(
        _MODULE,
        "_write_pty_input",
        lambda *_args, **_kwargs: calls.append("write:/exit"),
    )
    monkeypatch.setattr(
        _SUPERVISOR_MODULE,
        "teardown_subtree",
        lambda pid, *, issuer: calls.append(f"teardown:{pid}:{issuer}"),
    )

    reader._request_interactive_exit()

    assert handle.wait_timeouts == [_DEFAULT_INTERACTIVE_EXIT_GRACE_SECONDS]
    assert handle.terminate_calls == []
    assert calls[:3] == [
        "write:/exit",
        f"wait:{_DEFAULT_INTERACTIVE_EXIT_GRACE_SECONDS}",
        "poll",
    ]
