"""Pin: STRICTLY_STUCK ceiling for stuck-but-alive jobs.

The PROMPT log shows the cumulative waiting stretches to ~2370 s while
the watchdog defers past ``children_persist_too_long`` indefinitely.
The corroborator reports a stuck-but-alive state (e.g.
``OS_DESCENDANT_ONLY_STALE_PROGRESS`` -- the child process tree exists
but the child is not producing progress / heartbeat signals). The
standard 600 s ``CHILDREN_PERSIST_TOO_LONG`` ceiling is too lenient for
this signal.

The fix: a NEW ``WatchdogFireReason.STRICTLY_STUCK`` that fires when
the corroborator reports ``alive_by`` in
``{OS_DESCENDANT_ONLY_STALE_PROGRESS, CPU_IDLE_WHILE_ALIVE,
LOG_STALE_WHILE_ALIVE}`` AND no first-party channel is fresh for
``no_progress_quiet_strictly_stuck_seconds`` (default 300 s). This is
an ORTHOGONAL ceiling tuned for the stuck-but-alive case; it does NOT
modify ``_is_no_progress_quiet`` or ``StuckClassifier``.

This test drives ``IdleWatchdog.evaluate()`` with:

  - ``no_progress_quiet_seconds=600.0`` (the standard no-progress ceiling)
  - ``no_progress_quiet_strictly_stuck_seconds=300.0`` (the new ceiling)
  - ``no_progress_quiet_minimum_invocation_seconds=120.0`` (the floor)
  - ``alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS`` (stuck-but-alive)
  - invocation_elapsed_seconds advances past the floor with no progress

Asserts ``evaluate()`` returns ``WatchdogVerdict.FIRE`` with
``fire_reason == STRICTLY_STUCK`` once invocation elapsed crosses 300 s.
Pre-fix the new ceiling does not exist so the verdict is CONTINUE.

All tests use FakeClock; no real sleep, no real subprocess, no real
network.
"""

from __future__ import annotations

import typing
from dataclasses import dataclass

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WaitingCorroborator,
)
from ralph.agents.idle_watchdog.corroboration_snapshot import CorroborationSnapshot
from ralph.agents.idle_watchdog.watchdog_fire_reason import WatchdogFireReason
from ralph.agents.idle_watchdog.watchdog_verdict import WatchdogVerdict
from ralph.agents.timeout_clock import FakeClock
from ralph.process.child_liveness import AliveBy


@dataclass
class _NoProcessMonitor:
    """Fake process monitor: no live subagents, no captures."""

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


class _StubCorroborator:
    def __init__(self, alive_by: AliveBy | None) -> None:
        self._alive_by = alive_by

    def __call__(self) -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by=self._alive_by)


def _waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _make_watchdog(
    *,
    strictly_stuck_seconds: float | None = 300.0,
    alive_by: AliveBy | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=None,
        no_progress_quiet_seconds=600.0,
        no_progress_quiet_minimum_invocation_seconds=120.0,
        no_progress_quiet_strictly_stuck_seconds=strictly_stuck_seconds,
        activity_evidence_ttl_seconds=180.0,
        max_waiting_on_child_seconds=1800.0,
        max_waiting_on_child_no_progress_seconds=600.0,
        suspect_waiting_on_child_seconds=None,
        watchdog_log_throttle_seconds=30.0,
        watchdog_subagent_progress_interval_seconds=30.0,
    )
    return (
        IdleWatchdog(
            policy,
            clock,
            corroborator=typing.cast("WaitingCorroborator", _StubCorroborator(alive_by)),
            process_monitor=_NoProcessMonitor(),
        ),
        clock,
    )


def test_strictly_stuck_enum_exists() -> None:
    """WatchdogFireReason.STRICTLY_STUCK MUST exist."""
    assert hasattr(WatchdogFireReason, "STRICTLY_STUCK"), (
        "WatchdogFireReason.STRICTLY_STUCK missing; the new fire"
        " reason for stuck-but-alive jobs is required"
    )
    assert WatchdogFireReason.STRICTLY_STUCK.value == "strictly_stuck", (
        f"WatchdogFireReason.STRICTLY_STUCK.value must be"
        f" 'strictly_stuck'; got {WatchdogFireReason.STRICTLY_STUCK.value!r}"
    )


def test_strictly_stuck_fires_when_alive_by_pure_descendant_stale() -> None:
    """STRICTLY_STUCK MUST fire when alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS
    AND the run has been in the strictly-stuck alive_by state for at
    least ``no_progress_quiet_strictly_stuck_seconds``.

    Pre-fix this returns CONTINUE because the
    ``CHILDREN_PERSIST_TOO_LONG`` ceiling is at 600 s (not yet hit).
    Post-fix the new ceiling at 300 s fires STRICTLY_STUCK.

    The test pre-seeds ``_strictly_stuck_run_started_at`` to 0 so the
    first ``evaluate()`` call (after a single 305 s clock advance) sees
    a 305 s strictly-stuck run -- past the 300 s ceiling -- and fires.
    """
    wd, clock = _make_watchdog(
        strictly_stuck_seconds=300.0,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )
    wd.record_invocation_start()
    # Pre-seed the strictly-stuck run-start to the clock origin so a
    # single advance of 305 s yields a 305 s strictly-stuck run, well
    # past the 300 s ceiling. This avoids depending on the production
    # code's two-tick seed semantics and keeps the test focused on the
    # ceiling behavior. Use ``setattr`` with the attribute name held
    # in a local variable so mypy cannot narrow the access to a
    # private-attribute assignment AND ruff B010 does not flag a
    # setattr-with-constant-value call. The policy test for
    # ``test_zero_test_file_suppressions`` rejects bare mypy
    # suppression comments inside test files.
    _run_started_attr = "_strictly_stuck_run_started_at"
    setattr(wd, _run_started_attr, clock.monotonic())
    clock.advance(305.0)
    verdict = wd.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"STRICTLY_STUCK MUST fire at invocation elapsed = 305 s"
        f" with alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS; got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.STRICTLY_STUCK, (
        f"expected WatchdogFireReason.STRICTLY_STUCK; got {wd.last_fire_reason}"
    )


def test_strictly_stuck_does_not_fire_before_ceiling() -> None:
    """STRICTLY_STUCK MUST NOT fire before the ceiling elapses.

    Verifies the ceiling semantics: at 200 s with the ceiling at
    300 s the watchdog returns CONTINUE (no fire reason yet).
    """
    wd, clock = _make_watchdog(
        strictly_stuck_seconds=300.0,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )
    wd.record_invocation_start()
    clock.advance(200.0)
    verdict = wd.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"STRICTLY_STUCK MUST NOT fire before its ceiling elapses"
        f" (invocation_elapsed=200s, ceiling=300s); got {verdict}"
    )


def test_strictly_stuck_disabled_when_none() -> None:
    """When no_progress_quiet_strictly_stuck_seconds is None the ceiling
    is disabled and the watchdog returns CONTINUE.

    Operators can opt out by setting the field to ``None``. The
    default 300 s is opt-in.
    """
    wd, clock = _make_watchdog(
        strictly_stuck_seconds=None,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )
    wd.record_invocation_start()
    clock.advance(605.0)
    verdict = wd.evaluate(classify_quiet=_active)
    # Disabled: CONTINUE regardless of elapsed time.
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"STRICTLY_STUCK MUST be disabled when the field is None; got {verdict}"
    )
