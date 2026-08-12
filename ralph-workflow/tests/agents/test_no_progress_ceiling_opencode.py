"""No-progress ceiling integration test with the real OpenCode strategy.

Split from ``tests/agents/test_invoke_timeout_integration.py`` to keep both
modules under the 1000-line repo-structure cap.
"""

from __future__ import annotations

import re
import threading
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

from ralph.agents.execution_state import OpenCodeExecutionStrategy
from ralph.agents.idle_watchdog import (
    TimeoutPolicy,
    WaitingStatusEvent,
    WaitingStatusKind,
    WatchdogFireReason,
)
from ralph.agents.invoke import IdleStreamTimeoutError
from ralph.agents.timeout_clock import FakeClock
from ralph.process.liveness import FakeLivenessProbe
from tests.agents.test_invoke_timeout_integration import _FakeManagedHandle, _read_lines


def test_no_progress_ceiling_fires_with_opencode_strategy_os_descendants_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CHILDREN_PERSIST_TOO_LONG fires at no-progress ceiling with real OpenCodeExecutionStrategy.

    Regression for wt-97 Bug 1: an agent in WAITING_ON_CHILD with OS-descendant-only evidence
    (no scoped Ralph child registrations, alive_by=os_descendant_only_stale_progress) must fire
    at the shorter no-progress ceiling, not the full ceiling.

    Unlike test_no_progress_ceiling_fires_on_stale_child_liveness which uses a stub strategy,
    this test uses the real OpenCodeExecutionStrategy (empty registry, OS descendants present)
    to prove the end-to-end path from classify_quiet → corroborator → effective ceiling.
    """
    idle_timeout = 0.1
    max_waiting = 20.0
    no_progress_ceiling = 10.0
    status_interval = 100.0

    # Same broken-agent-grace isolation as the sibling stale-liveness
    # test: the pinned 10s no-progress ceiling fires BEFORE the 12s
    # module default grace window; the monkeypatch keeps the scenario
    # robust to future default tuning.
    import ralph.agents.invoke._process_reader as _process_reader_module

    monkeypatch.setattr(_process_reader_module, "BROKEN_AGENT_OUTPUT_GRACE_SECONDS", 900.0)

    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        max_waiting_on_child_seconds=max_waiting,
        max_waiting_on_child_no_progress_seconds=no_progress_ceiling,
        drain_window_seconds=0.0,
        idle_poll_interval_seconds=0.05,
        waiting_status_interval_seconds=status_interval,
        suspect_waiting_on_child_seconds=None,
        stuck_job_sub_ceiling_seconds=None,
        # Disable OS-descendant-only ceiling (its default is larger than max_waiting)
        os_descendant_only_ceiling_seconds=None,
        no_progress_quiet_seconds=None,
        # R1 (Trustworthy Idle Watchdog spec): disable the process
        # monitor so the fallback path (which reads ``descendant_snapshot``)
        # is the source of ``scoped_child_active`` for this legacy
        # test of the no-progress ceiling branch. See the parallel
        # change in ``test_no_progress_ceiling_fires_on_stale_child_liveness``
        # for the full rationale.
        process_monitor_enabled=False,
    )
    clock = FakeClock(start=0.0)
    _reader_release = threading.Event()

    def _blocking_stdout() -> Iterator[str]:
        _reader_release.wait(timeout=5.0)
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
        with pytest.raises(IdleStreamTimeoutError) as exc_info:
            for _ in _read_lines(
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
    assert diag.get("effective_ceiling_label") == "no_progress", (
        f"Expected effective_ceiling_label='no_progress', got {diag.get('effective_ceiling_label')}"
    )
    assert diag.get("alive_by") == "os_descendant_only_stale_progress", (
        f"Expected alive_by='os_descendant_only_stale_progress', got {diag.get('alive_by')}"
    )
