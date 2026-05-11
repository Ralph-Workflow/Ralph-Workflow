"""Black-box integration tests for _read_lines_from_process timeout behavior.

These tests drive the read loop with a fake stdout pipe and FakeClock to prove:
  (a) SESSION_CEILING_EXCEEDED fires under continuous output (false-negative fix).
  (b) NO_OUTPUT_DEADLINE fires when classify_quiet raises (defensive-wrap fix).
  (c) CHILDREN_PERSIST_TOO_LONG fires with oscillating heartbeat (absolute-ceiling fix).

No real subprocesses are spawned; all timing is driven by FakeClock.
"""

from __future__ import annotations

import re
import threading
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

from ralph.agents.execution_state import (
    AgentExecutionState,
    GenericExecutionStrategy,
    OpenCodeExecutionStrategy,
)
from ralph.agents.idle_watchdog import (
    TimeoutPolicy,
    WaitingStatusEvent,
    WaitingStatusKind,
    WatchdogFireReason,
)
from ralph.agents.invoke import _IdleStreamTimeoutError, _read_lines_from_process
from ralph.agents.timeout_clock import FakeClock
from ralph.process.liveness import FakeLivenessProbe


class _FakeManagedHandle:
    """Minimal test double for ManagedProcess used by _read_lines_from_process."""

    def __init__(
        self,
        stdout_lines: object,
        *,
        descendant_count: int = 0,
        descendant_oldest_seconds: float = 0.0,
    ) -> None:
        self.stdout = stdout_lines
        self.stderr = None
        self.returncode: int | None = 0
        self._terminated = False
        self._descendant_count = descendant_count
        self._descendant_oldest_seconds = descendant_oldest_seconds

    def poll(self) -> int | None:
        return 0

    def terminate(self, grace_period_s: float = 0.5) -> None:
        self._terminated = True

    def has_live_descendants(self) -> bool:
        return self._descendant_count > 0

    def descendant_snapshot(self) -> tuple[int, float]:
        """Return (count, oldest_seconds) for corroborator to determine alive_by."""
        return (self._descendant_count, self._descendant_oldest_seconds)

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


class _WaitingStrategy(GenericExecutionStrategy):
    """Strategy whose classify_quiet always returns WAITING_ON_CHILD."""

    def classify_quiet(
        self,
        handle: object,
        liveness_probe: object,
    ) -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD


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
        idle_poll_interval_seconds=0.01,
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
    """CHILDREN_PERSIST_TOO_LONG fires when classify_quiet raises repeatedly.

    _safe_classify_quiet now returns WAITING_ON_CHILD on exception (not ACTIVE),
    so the watchdog defers to the cumulative WAITING ceiling rather than firing
    NO_OUTPUT_DEADLINE immediately. The read loop eventually fires once cumulative
    WAITING time exceeds max_waiting_on_child_seconds.

    The stdout is a blocking generator so the read loop takes only the empty-queue
    path (where _safe_classify_quiet is invoked).  The _reader_release event is
    set in a finally block so the reader daemon thread exits cleanly.
    """
    idle_timeout = 0.2
    max_waiting = 0.4
    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        max_waiting_on_child_seconds=max_waiting,
        drain_window_seconds=0.0,
        idle_poll_interval_seconds=0.01,
        suspect_waiting_on_child_seconds=None,
        # Disable no-progress ceiling to avoid validation issues with small max_waiting
        max_waiting_on_child_no_progress_seconds=None,
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

    assert exc_info.value.reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
    assert clock.monotonic() >= idle_timeout + max_waiting


def test_classify_quiet_exception_defers_not_fires() -> None:
    """classify_quiet raising defers to WAITING ceiling, not NO_OUTPUT_DEADLINE.

    _safe_classify_quiet returns WAITING_ON_CHILD (not ACTIVE) on exception, so
    NO_OUTPUT_DEADLINE is never raised when classify_quiet always raises. Instead
    the watchdog defers until the cumulative WAITING ceiling fires.
    """
    idle_timeout = 0.2
    max_waiting = 0.4
    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        max_waiting_on_child_seconds=max_waiting,
        drain_window_seconds=0.05,
        idle_poll_interval_seconds=0.01,
        suspect_waiting_on_child_seconds=None,
        # Disable no-progress ceiling to avoid validation issues with small max_waiting
        max_waiting_on_child_no_progress_seconds=None,
    )
    clock = FakeClock(start=0.0)
    _reader_release = threading.Event()

    def _blocking_stdout() -> Iterator[str]:
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

    # Must be CHILDREN_PERSIST_TOO_LONG, NOT NO_OUTPUT_DEADLINE.
    assert exc_info.value.reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
    assert exc_info.value.reason != WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_post_yield_evaluate_uses_real_classify_quiet() -> None:
    """Whitespace-only line post-yield does not fire when children are alive.

    Previously the post-yield evaluate forced classify_quiet=lambda: ACTIVE, so
    a whitespace-only line (no record_activity call) past the idle deadline would
    drive the watchdog into the ACTIVE branch and fire NO_OUTPUT_DEADLINE even when
    children are present. Now the real classify_quiet is consulted.
    """
    idle_timeout = 2.0
    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=0.5,
        idle_poll_interval_seconds=0.01,
    )
    clock = FakeClock(start=0.0)

    def _stdout_gen() -> Iterator[str]:
        # Advance clock past idle_timeout before line lands in queue so the
        # post-yield evaluate sees idle_elapsed >= idle_timeout.
        clock.advance(3.0)
        yield "   \n"  # whitespace-only: classify_activity_line returns None
        # Generator exhausted; reader thread exits naturally.

    handle = _FakeManagedHandle(_stdout_gen())

    # Should NOT raise _IdleStreamTimeoutError: _WaitingStrategy.classify_quiet
    # returns WAITING_ON_CHILD so the post-yield evaluate defers, and the reader
    # exits cleanly before the cumulative ceiling is reached.
    lines = list(
        _read_lines_from_process(
            handle,
            policy=policy,
            execution_strategy=_WaitingStrategy(),
            _clock=clock,
        )
    )
    assert "   \n" in lines


def test_cumulative_ceiling_fires_with_oscillating_heartbeat() -> None:
    """Black-box reproduction of the user's bug: an agent that produces a heartbeat
    after every idle deadline must still trip the absolute CHILDREN_PERSIST_TOO_LONG
    ceiling.

    Design: the generator yields one meaningful line every (idle_timeout + 1) seconds
    of fake-clock time by busy-waiting on clock.monotonic() (yielding CPU via
    time.sleep(0) between checks so the main loop can advance the FakeClock through
    wait_for_event calls).  classify_quiet always returns WAITING_ON_CHILD.

    With the old code the heartbeat + drain_window quiet reset cumulative to 0 every
    cycle, so the loop ran forever.  With the fix cumulative is absolute and grows by
    ~1s per cycle; it eventually exceeds max_waiting_on_child_seconds and
    CHILDREN_PERSIST_TOO_LONG fires.
    """
    idle_timeout = 1.0
    max_waiting = 2.0
    drain_window = 0.05
    poll_interval = 0.01

    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        max_waiting_on_child_seconds=max_waiting,
        drain_window_seconds=drain_window,
        idle_poll_interval_seconds=poll_interval,
        suspect_waiting_on_child_seconds=None,
        # Disable no-progress ceiling to avoid validation issues with small max_waiting
        max_waiting_on_child_no_progress_seconds=None,
    )
    clock = FakeClock(start=0.0)

    _release = threading.Event()
    _max_heartbeats = 500  # safety: must fire long before this

    def _oscillating_stdout() -> Iterator[str]:
        # Yield a meaningful heartbeat line every (idle_timeout + 1.0) fake-clock
        # seconds.  Between yields, busy-wait with time.sleep(0) to release the
        # GIL so the main read-loop thread can advance the FakeClock.
        for heartbeat_num in range(_max_heartbeats):
            target_t = (heartbeat_num + 1) * (idle_timeout + 0.1)
            while clock.monotonic() < target_t:
                time.sleep(0)  # release GIL; main thread advances clock
            yield f"heartbeat {heartbeat_num}\n"
        # Safety tail: release so reader thread exits cleanly if fire never fires.
        _release.wait(timeout=5.0)
        yield from ()

    handle = _FakeManagedHandle(_oscillating_stdout())

    try:
        with pytest.raises(_IdleStreamTimeoutError) as exc_info:
            for _ in _read_lines_from_process(
                handle,
                policy=policy,
                execution_strategy=_WaitingStrategy(),
                _clock=clock,
            ):
                pass
    finally:
        _release.set()

    assert exc_info.value.reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# ---------------------------------------------------------------------------
# New integration test: listener wiring + no per-tick spam
# ---------------------------------------------------------------------------

_MAX_PROGRESS_EVENTS = 3
_MAX_TOTAL_EVENTS = 6


def test_invoke_emits_waiting_listener_events_not_per_tick_log() -> None:
    """_read_lines_from_process emits structured listener events, not per-tick debug spam.

    Asserts:
    - Exactly 1 ENTERED event.
    - At least 1 PROGRESS event.
    - At most 3 PROGRESS events (proves throttling — status_interval=1.0s over ~2s).
    - Exactly 1 HARD_STOP event.
    - Total events <= 6.
    """
    idle_timeout = 0.2
    max_waiting = 0.6
    status_interval = 0.2

    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        max_waiting_on_child_seconds=max_waiting,
        drain_window_seconds=0.0,
        idle_poll_interval_seconds=0.05,
        waiting_status_interval_seconds=status_interval,
        suspect_waiting_on_child_seconds=None,
        # Disable no-progress ceiling to avoid validation issues with small max_waiting
        max_waiting_on_child_no_progress_seconds=None,
    )
    clock = FakeClock(start=0.0)

    _reader_release = threading.Event()

    def _blocking_stdout() -> Iterator[str]:
        # Blocks the reader thread so the main loop takes the empty-queue path.
        _reader_release.wait()
        yield from ()

    handle = _FakeManagedHandle(_blocking_stdout())
    captured_events: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured_events.append(event)

    try:
        with pytest.raises(_IdleStreamTimeoutError) as exc_info:
            for _ in _read_lines_from_process(
                handle,
                policy=policy,
                execution_strategy=_WaitingStrategy(),
                waiting_listener=_listener,
                _clock=clock,
            ):
                pass
    finally:
        _reader_release.set()

    assert exc_info.value.reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    entered = [e for e in captured_events if e.kind == WaitingStatusKind.ENTERED]
    progress = [e for e in captured_events if e.kind == WaitingStatusKind.PROGRESS]
    hard_stops = [e for e in captured_events if e.kind == WaitingStatusKind.HARD_STOP]

    assert len(entered) == 1, f"Expected 1 ENTERED, got {len(entered)}"
    assert len(progress) >= 1, f"Expected >=1 PROGRESS, got {len(progress)}"
    assert len(progress) <= _MAX_PROGRESS_EVENTS, (
        f"Expected <={_MAX_PROGRESS_EVENTS} PROGRESS (throttled), got {len(progress)}"
    )
    assert len(hard_stops) == 1, f"Expected 1 HARD_STOP, got {len(hard_stops)}"
    assert len(captured_events) <= _MAX_TOTAL_EVENTS, (
        f"Expected <={_MAX_TOTAL_EVENTS} total events, got {len(captured_events)}"
    )


# ---------------------------------------------------------------------------
# Corroboration diagnostic in HARD_STOP (Phase 3 integration)
# ---------------------------------------------------------------------------


def test_children_persist_hard_stop_includes_corroboration_diagnostic() -> None:
    """HARD_STOP event and _IdleStreamTimeoutError message contain corroboration fields.

    Asserts:
    - _IdleStreamTimeoutError message contains 'cumulative=' and 'scoped_child_active='.
    - Captured HARD_STOP WaitingStatusEvent.diagnostic includes 'evidence' and 'cumulative'.
    """
    idle_timeout = 0.2
    max_waiting = 0.6
    status_interval = 100.0  # suppress PROGRESS noise

    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        max_waiting_on_child_seconds=max_waiting,
        drain_window_seconds=0.0,
        idle_poll_interval_seconds=0.05,
        waiting_status_interval_seconds=status_interval,
        suspect_waiting_on_child_seconds=None,
        # Disable no-progress ceiling to avoid validation issues with small max_waiting
        max_waiting_on_child_no_progress_seconds=None,
    )
    clock = FakeClock(start=0.0)

    _reader_release = threading.Event()

    def _blocking_stdout() -> Iterator[str]:
        _reader_release.wait()
        yield from ()

    handle = _FakeManagedHandle(_blocking_stdout())
    captured_events: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured_events.append(event)

    try:
        with pytest.raises(_IdleStreamTimeoutError) as exc_info:
            for _ in _read_lines_from_process(
                handle,
                policy=policy,
                execution_strategy=_WaitingStrategy(),
                waiting_listener=_listener,
                _clock=clock,
            ):
                pass
    finally:
        _reader_release.set()

    assert exc_info.value.reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    # Error message must contain core corroboration fields
    err_msg = str(exc_info.value)
    assert "cumulative=" in err_msg, f"Expected 'cumulative=' in: {err_msg}"
    assert "scoped_child_active=" in err_msg, f"Expected 'scoped_child_active=' in: {err_msg}"

    # HARD_STOP event diagnostic must contain timing fields
    hard_stops = [e for e in captured_events if e.kind == WaitingStatusKind.HARD_STOP]
    assert len(hard_stops) == 1, f"Expected 1 HARD_STOP, got {len(hard_stops)}"
    diag = hard_stops[0].diagnostic
    assert "cumulative" in diag, f"Expected 'cumulative' key in diagnostic: {diag}"
    assert "evidence" in diag, f"Expected 'evidence' key in HARD_STOP diagnostic: {diag}"


# ---------------------------------------------------------------------------
# No-progress ceiling integration test (wt-97-timeout regression)
# ---------------------------------------------------------------------------


def test_no_progress_ceiling_fires_on_stale_child_liveness() -> None:
    """CHILDREN_PERSIST_TOO_LONG fires at no-progress ceiling when child is alive but stale.

    Regression test for wt-97-timeout: an agent in WAITING_ON_CHILD with
    os_descendant_only_stale_progress evidence (alive child with stale/no fresh progress)
    must fire at the shorter no-progress ceiling instead of the full ceiling.

    Design:
    - max_waiting_on_child_seconds=100.0 (full ceiling)
    - max_waiting_on_child_no_progress_seconds=10.0 (no-progress ceiling)
    - Handle reports active descendants but no fresh registry progress.
    - Corroborator will set alive_by='os_descendant_only_stale_progress'.
    - Watchdog must fire at ~10s (no-progress ceiling), not 100s (full ceiling).
    """
    idle_timeout = 0.5
    max_waiting = 100.0
    no_progress_ceiling = 10.0
    status_interval = 100.0  # suppress PROGRESS noise

    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        max_waiting_on_child_seconds=max_waiting,
        max_waiting_on_child_no_progress_seconds=no_progress_ceiling,
        drain_window_seconds=0.0,
        idle_poll_interval_seconds=0.05,
        waiting_status_interval_seconds=status_interval,
        suspect_waiting_on_child_seconds=None,
    )
    clock = FakeClock(start=0.0)

    _reader_release = threading.Event()

    def _blocking_stdout() -> Iterator[str]:
        _reader_release.wait()
        yield from ()

    # Fake handle with active descendants but no fresh progress.
    # This triggers alive_by='os_descendant_only_stale_progress' in the corroborator.
    handle = _FakeManagedHandle(
        _blocking_stdout(),
        descendant_count=1,
        descendant_oldest_seconds=5.0,
    )
    captured_events: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured_events.append(event)

    try:
        with pytest.raises(_IdleStreamTimeoutError) as exc_info:
            for _ in _read_lines_from_process(
                handle,
                policy=policy,
                execution_strategy=_WaitingStrategy(),
                waiting_listener=_listener,
                _clock=clock,
            ):
                pass
    finally:
        _reader_release.set()

    assert exc_info.value.reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    # Must have fired at the no-progress ceiling (~10s), NOT the full ceiling (~100s).
    # The error message should indicate the no-progress ceiling was used.
    err_msg = str(exc_info.value)
    assert "cumulative=" in err_msg, f"Expected 'cumulative=' in: {err_msg}"
    # Extract cumulative value to verify it fired before the full ceiling
    match = re.search(r"cumulative=([\d.]+)s", err_msg)
    assert match is not None, f"Could not find cumulative value in: {err_msg}"
    cumulative = float(match.group(1))
    assert cumulative < max_waiting, (
        f"Expected to fire before full ceiling ({max_waiting}s), but cumulative={cumulative}s"
    )
    assert cumulative >= no_progress_ceiling, (
        f"Expected to fire at or after no-progress ceiling ({no_progress_ceiling}s), "
        f"but cumulative={cumulative}s"
    )

    # HARD_STOP diagnostic must include effective_ceiling classification.
    hard_stops = [e for e in captured_events if e.kind == WaitingStatusKind.HARD_STOP]
    assert len(hard_stops) == 1, f"Expected 1 HARD_STOP, got {len(hard_stops)}"
    diag = hard_stops[0].diagnostic
    assert "effective_ceiling" in diag, (
        f"Expected 'effective_ceiling' key in HARD_STOP diagnostic: {diag}"
    )
    assert diag["effective_ceiling"] == "no_progress", (
        f"Expected effective_ceiling='no_progress', got {diag.get('effective_ceiling')}"
    )
    assert "alive_by" in diag, f"Expected 'alive_by' key in diagnostic: {diag}"
    assert diag["alive_by"] == "os_descendant_only_stale_progress", (
        f"Expected alive_by='os_descendant_only_stale_progress', got {diag.get('alive_by')}"
    )


def test_no_progress_ceiling_fires_with_opencode_strategy_os_descendants_only() -> None:
    """CHILDREN_PERSIST_TOO_LONG fires at no-progress ceiling with real OpenCodeExecutionStrategy.

    Regression for wt-97 Bug 1: an agent in WAITING_ON_CHILD with OS-descendant-only evidence
    (no scoped Ralph child registrations, alive_by=os_descendant_only_stale_progress) must fire
    at the shorter no-progress ceiling, not the full ceiling.

    Unlike test_no_progress_ceiling_fires_on_stale_child_liveness which uses a stub strategy,
    this test uses the real OpenCodeExecutionStrategy (empty registry, OS descendants present)
    to prove the end-to-end path from classify_quiet → corroborator → effective ceiling.
    """
    idle_timeout = 0.5
    max_waiting = 100.0
    no_progress_ceiling = 10.0
    status_interval = 100.0

    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        max_waiting_on_child_seconds=max_waiting,
        max_waiting_on_child_no_progress_seconds=no_progress_ceiling,
        drain_window_seconds=0.0,
        idle_poll_interval_seconds=0.05,
        waiting_status_interval_seconds=status_interval,
        suspect_waiting_on_child_seconds=None,
    )
    clock = FakeClock(start=0.0)
    _reader_release = threading.Event()

    def _blocking_stdout() -> Iterator[str]:
        _reader_release.wait()
        yield from ()

    handle = _FakeManagedHandle(
        _blocking_stdout(),
        descendant_count=1,
        descendant_oldest_seconds=5.0,
    )

    # Real OpenCodeExecutionStrategy with no registered children.
    # classify_quiet will fall back to OS descendants → WAITING_ON_CHILD.
    # corroborator will see no registry → scoped_active=True → alive_by=OS_DESCENDANT_ONLY.
    strategy = OpenCodeExecutionStrategy()
    # No scoped Ralph evidence: probe returns no active children.
    probe = FakeLivenessProbe(active=False)
    captured_events: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured_events.append(event)

    try:
        with pytest.raises(_IdleStreamTimeoutError) as exc_info:
            for _ in _read_lines_from_process(
                handle,
                policy=policy,
                execution_strategy=strategy,
                liveness_probe=probe,
                waiting_listener=_listener,
                _clock=clock,
            ):
                pass
    finally:
        _reader_release.set()

    assert exc_info.value.reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    # Must fire at no-progress ceiling (~10s), NOT the full ceiling (~100s).
    err_msg = str(exc_info.value)
    match = re.search(r"cumulative=([\.\d]+)s", err_msg)
    assert match is not None, f"Expected 'cumulative=' in: {err_msg}"
    cumulative = float(match.group(1))
    assert cumulative < max_waiting, (
        f"Expected to fire before full ceiling ({max_waiting}s), but cumulative={cumulative}s"
    )
    assert cumulative >= no_progress_ceiling, (
        f"Expected to fire at or after no-progress ceiling ({no_progress_ceiling}s), "
        f"but cumulative={cumulative}s"
    )

    # HARD_STOP diagnostic must confirm OS-descendant-only evidence and no-progress ceiling.
    hard_stops = [e for e in captured_events if e.kind == WaitingStatusKind.HARD_STOP]
    assert len(hard_stops) == 1, f"Expected 1 HARD_STOP, got {len(hard_stops)}"
    diag = hard_stops[0].diagnostic
    assert diag.get("effective_ceiling") == "no_progress", (
        f"Expected effective_ceiling='no_progress', got {diag.get('effective_ceiling')}"
    )
    assert diag.get("alive_by") == "os_descendant_only_stale_progress", (
        f"Expected alive_by='os_descendant_only_stale_progress', got {diag.get('alive_by')}"
    )
