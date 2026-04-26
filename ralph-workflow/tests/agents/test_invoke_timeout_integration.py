"""Black-box integration tests for _read_lines_from_process timeout behavior.

These tests drive the read loop with a fake stdout pipe and FakeClock to prove:
  (a) SESSION_CEILING_EXCEEDED fires under continuous output (false-negative fix).
  (b) NO_OUTPUT_DEADLINE fires when classify_quiet raises (defensive-wrap fix).

No real subprocesses are spawned; all timing is driven by FakeClock.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

from ralph.agents.execution_state import AgentExecutionState, GenericExecutionStrategy
from ralph.agents.idle_watchdog import TimeoutPolicy, WatchdogFireReason
from ralph.agents.invoke import _IdleStreamTimeoutError, _read_lines_from_process
from ralph.agents.timeout_clock import FakeClock


class _FakeManagedHandle:
    """Minimal test double for ManagedProcess used by _read_lines_from_process."""

    def __init__(self, stdout_lines: object) -> None:
        self.stdout = stdout_lines
        self.stderr = None
        self.returncode: int | None = 0
        self._terminated = False

    def poll(self) -> int | None:
        return 0

    def terminate(self, grace_period_s: float = 0.5) -> None:
        self._terminated = True

    def has_live_descendants(self) -> bool:
        return False

    def __enter__(self) -> _FakeManagedHandle:
        return self

    def __exit__(self, *args: object) -> None:
        pass


class _RaisingStrategy(GenericExecutionStrategy):
    """Strategy whose classify_quiet always raises to simulate a transient probe failure."""

    def classify_quiet(
        self,
        handle: object,
        liveness_probe: object,
    ) -> AgentExecutionState:
        raise RuntimeError("boom")


_MAX_SESSION_SECONDS = 5.0
_CLOCK_ADVANCE_PER_LINE = 1.0
_TOTAL_LINES_IN_STDOUT = 20


def test_session_ceiling_fires_under_continuous_output() -> None:
    """SESSION_CEILING_EXCEEDED fires even when the agent produces continuous output.

    Before the fix, evaluate() was skipped after each line yield via 'continue',
    so SESSION_CEILING_EXCEEDED never fired during continuous output.  With the
    fix, evaluate() runs on every loop iteration including the post-yield path.

    The key assertion: the exception IS raised with SESSION_CEILING_EXCEEDED.
    Without the fix, the generator returns normally (no exception) because the
    'continue' bypasses all evaluate() calls and the reader exhausts stdout.
    """
    policy = TimeoutPolicy(
        idle_timeout_seconds=_MAX_SESSION_SECONDS,
        max_session_seconds=_MAX_SESSION_SECONDS,
        idle_poll_interval_seconds=0.05,
        drain_window_seconds=0.0,
    )
    clock = FakeClock(start=0.0)

    def _stdout_gen() -> Iterator[str]:
        for i in range(_TOTAL_LINES_IN_STDOUT):
            clock.advance(_CLOCK_ADVANCE_PER_LINE)
            yield f"line {i}\n"

    handle = _FakeManagedHandle(_stdout_gen())

    with pytest.raises(_IdleStreamTimeoutError) as exc_info:
        for _ in _read_lines_from_process(handle, policy=policy, _clock=clock):
            pass

    assert exc_info.value.reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
    assert exc_info.value.timeout_seconds == _MAX_SESSION_SECONDS


def test_watchdog_fires_even_when_classify_quiet_raises() -> None:
    """NO_OUTPUT_DEADLINE fires even when classify_quiet raises on every call.

    Before the fix, a RuntimeError from classify_quiet propagated out of evaluate()
    and crashed the read loop without ever raising _IdleStreamTimeoutError.  With
    the defensive _safe_classify_quiet shim, the exception is caught and ACTIVE is
    returned, allowing the idle deadline to fire normally.

    The stdout is a blocking generator so the read loop takes only the empty-queue
    path (where _safe_classify_quiet is invoked).  The _reader_release event is
    set in a finally block so the reader daemon thread exits cleanly.
    """
    idle_timeout = 2.0
    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=0.0,
        idle_poll_interval_seconds=0.05,
    )
    clock = FakeClock(start=0.0)

    _reader_release = threading.Event()

    def _blocking_stdout() -> Iterator[str]:
        # Blocks reader thread so main loop takes the empty-queue/evaluate path.
        # Released in finally so reader thread exits cleanly after the test.
        _reader_release.wait()
        yield from ()

    handle = _FakeManagedHandle(_blocking_stdout())

    try:
        with pytest.raises(_IdleStreamTimeoutError) as exc_info:
            for _ in _read_lines_from_process(
                handle,
                policy=policy,
                execution_strategy=_RaisingStrategy(),
                _clock=clock,
            ):
                pass
    finally:
        _reader_release.set()

    assert exc_info.value.reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    assert clock.monotonic() >= idle_timeout
