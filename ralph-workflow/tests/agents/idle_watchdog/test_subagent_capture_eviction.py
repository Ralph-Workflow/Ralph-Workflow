"""Black-box tests for the FIFO-bounded subagent output capture cache.

wt-024 Step 6 (AC-04): ``_subagent_output_captures`` in
``IdleWatchdog`` is an ``OrderedDict`` capped at
``_MAX_SUBAGENT_OUTPUT_CAPTURES = 128`` (defined in
``_activity_methods``). Inserting more than the cap evicts the
oldest entry (FIFO), so a single high-fan-out watchdog tick
that sees many distinct worker IDs cannot grow the dict
unboundedly within one invocation.

The tests drive the production PUBLIC entry point
:meth:`IdleWatchdog.poll_subagent_output` with a fake
``ProcessMonitor`` whose ``discover_subagent_outputs`` returns
a deterministic mapping of fresh worker_ids to fresh
``SubagentOutputCapture`` instances on every call. No direct
``OrderedDict`` mutation, no direct ``_subagent_output_captures``
assignment, no real subprocess, no real file I/O, no
``time.sleep``. ``FakeClock`` drives the watchdog's clock
deterministically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    _activity_methods,
)
from ralph.agents.timeout_clock import FakeClock

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.process.monitor._subagent_output_capture import SubagentOutputCapture


class _StaticCapture:
    """A SubagentOutputCapture that returns one line per ``read_lines`` call."""

    def __init__(self) -> None:
        self.read_count = 0

    def read_lines(self, worker_id: str) -> list[str]:
        self.read_count += 1
        return [f"line-for-{worker_id}"]


class _StaticCaptureEmpty:
    """A SubagentOutputCapture that returns no lines (so poll_subagent_output
    is a no-op for count).
    """

    def read_lines(self, worker_id: str) -> list[str]:
        del worker_id
        return []


class _FakeProcessMonitor:
    """ProcessMonitor whose ``discover_subagent_outputs`` is callable-driven."""

    def __init__(self, captures: Mapping[str, SubagentOutputCapture]) -> None:
        self._captures = dict(captures)
        self.discover_calls = 0

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        self.discover_calls += 1
        return dict(self._captures)

    def live_subagent_count(self) -> int:
        return len(self._captures)

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass


def _make_watchdog(
    monitor: _FakeProcessMonitor,
) -> tuple[IdleWatchdog, FakeClock]:
    config = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        drain_window_seconds=0.0,
        max_waiting_on_child_seconds=1800.0,
        no_progress_quiet_seconds=240.0,
        no_progress_quiet_heartbeat_ceiling_seconds=240.0,
    )
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        config,
        clock,
        process_monitor=monitor,
    )
    watchdog.record_invocation_start()
    return watchdog, clock


def test_subagent_capture_cache_is_bounded_by_cap() -> None:
    """FIFO eviction: oldest workers dropped when the cap is exceeded.

    Drives the PUBLIC :meth:`IdleWatchdog.poll_subagent_output`
    once with a monitor that returns cap+5 distinct workers.
    The watchdog's private ``_subagent_output_captures`` cache
    (observable via the same ``len()`` accessor used by other
    tests) MUST be capped at ``_MAX_SUBAGENT_OUTPUT_CAPTURES``
    so it cannot grow unboundedly within one invocation.
    """
    cap = _activity_methods._MAX_SUBAGENT_OUTPUT_CAPTURES

    captures = {f"w-{i}": _StaticCaptureEmpty() for i in range(cap + 5)}
    monitor = _FakeProcessMonitor(captures)

    watchdog, clock = _make_watchdog(monitor)
    fresh = watchdog.poll_subagent_output(now=clock.monotonic())

    # The watchdog processed every discovered worker, but the
    # internal cache MUST stay at the cap (FIFO eviction).
    assert fresh == 0, "empty captures produce no fresh lines"
    assert monitor.discover_calls == 1
    assert len(watchdog._subagent_output_captures) == cap, (
        f"capture cache MUST be capped at {cap}, got {len(watchdog._subagent_output_captures)}"
    )
    # The 5 oldest workers (w-0..w-4) MUST be evicted.
    for index in range(5):
        assert f"w-{index}" not in watchdog._subagent_output_captures
    # The newest cap workers MUST be retained.
    for index in range(5, cap + 5):
        assert f"w-{index}" in watchdog._subagent_output_captures


def test_subagent_capture_cache_does_not_evict_when_under_cap() -> None:
    """Inserts under the cap never evict anything."""
    cap = _activity_methods._MAX_SUBAGENT_OUTPUT_CAPTURES
    captures = {f"keep-{i}": _StaticCaptureEmpty() for i in range(cap // 2)}
    monitor = _FakeProcessMonitor(captures)

    watchdog, clock = _make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())

    assert len(watchdog._subagent_output_captures) == cap // 2
    for index in range(cap // 2):
        assert f"keep-{index}" in watchdog._subagent_output_captures


def test_subagent_capture_cache_eviction_skips_existing_workers() -> None:
    """A repeated discover call for already-cached workers does NOT trigger eviction.

    Drives the PUBLIC entry point twice with the same worker
    set. The second call MUST NOT grow the cache (the existing
    workers are reused), and the cap MUST hold.
    """
    cap = _activity_methods._MAX_SUBAGENT_OUTPUT_CAPTURES

    primed = {f"primed-{i}": _StaticCaptureEmpty() for i in range(cap)}
    monitor = _FakeProcessMonitor(primed)
    watchdog, clock = _make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog._subagent_output_captures) == cap

    # Same workers, second tick: no new workers, no eviction.
    clock.advance(0.01)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog._subagent_output_captures) == cap


def test_subagent_capture_cache_processes_all_workers_via_poll() -> None:
    """A poll_subagent_output tick with N+5 workers records N+5 fresh lines.

    The cap is enforced on the cache, but the PUBLIC surface
    still reports EVERY worker's lines (the cap is an internal
    caching optimization, not a sampling cap). This pins the
    observable behavior: ``_subagent_output_count`` advances
    by exactly N+5 even when the cache is capped at N.
    """
    cap = _activity_methods._MAX_SUBAGENT_OUTPUT_CAPTURES

    captures = {f"w-{i}": _StaticCapture() for i in range(cap + 5)}
    monitor = _FakeProcessMonitor(captures)

    watchdog, clock = _make_watchdog(monitor)
    fresh = watchdog.poll_subagent_output(now=clock.monotonic())

    # Every discovered worker's capture is read once (one line each).
    assert fresh == cap + 5
    # The watchdog records the total in the public counter.
    assert watchdog._subagent_output_count == cap + 5
    # The internal cache is FIFO-bounded at the cap.
    assert len(watchdog._subagent_output_captures) == cap
