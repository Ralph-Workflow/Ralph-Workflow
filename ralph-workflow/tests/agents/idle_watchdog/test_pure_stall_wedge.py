"""Regression tests for the pure-stall false-negative wedge.

When the agent produces ZERO activity on any channel (no stdout, no MCP tool
call, no workspace event, no subagent progress) the watchdog must not miss the
fire.  These tests pin:

* ``NO_PROGRESS_QUIET`` fires once ``no_progress_quiet_seconds`` elapse while
  the agent is in ``WAITING_ON_CHILD`` and the corroborator reports no live
  child signal at all.
* ``NO_OUTPUT_AT_START`` fires once ``no_output_at_start_seconds`` elapse with
  zero activity at invocation start.

All tests use FakeClock and injected corroborators; no real subprocess, no
real sleep, no real network.
"""

from __future__ import annotations

from dataclasses import dataclass

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    CorroborationSnapshot,
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.timeout_clock import FakeClock

_IDLE_TIMEOUT_SECONDS = 300.0
_SILENT_SUBAGENT_SECONDS = 180.0
_NO_PROGRESS_QUIET_SECONDS = 60.0
_NO_OUTPUT_AT_START_SECONDS = 30.0


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


def _waiting_on_child() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _make_watchdog() -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=_IDLE_TIMEOUT_SECONDS,
        no_output_at_start_seconds=_NO_OUTPUT_AT_START_SECONDS,
        no_progress_quiet_seconds=_NO_PROGRESS_QUIET_SECONDS,
        no_progress_quiet_minimum_invocation_seconds=None,
        no_progress_quiet_heartbeat_ceiling_seconds=None,
        silent_subagent_seconds=_SILENT_SUBAGENT_SECONDS,
        activity_evidence_ttl_seconds=30.0,
    )

    def _no_live_child_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by=None)

    return (
        IdleWatchdog(
            policy,
            clock,
            process_monitor=_NoProcessMonitor(),
            corroborator=_no_live_child_corroborator,
        ),
        clock,
    )


def test_zero_activity_past_no_progress_quiet_fires() -> None:
    """Zero activity past no_progress_quiet_seconds MUST fire NO_PROGRESS_QUIET.

    The corroborator reports no live child signal (``alive_by=None``), so the
    watchdog cannot defer to the cumulative ``CHILDREN_PERSIST_TOO_LONG"
    ceiling.  Calls at t=30 and t=59 return CONTINUE; the call at t=60 returns
    FIRE with ``last_fire_reason == NO_PROGRESS_QUIET``.
    """
    watchdog, clock = _make_watchdog()
    watchdog.record_invocation_start()

    for elapsed in (30.0, 59.0):
        clock.advance(elapsed - clock.monotonic())
        verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)
        assert verdict == WatchdogVerdict.CONTINUE, (
            f"expected CONTINUE at t={elapsed}; got {verdict}"
        )
        assert watchdog.last_fire_reason is None

    clock.advance(60.0 - clock.monotonic())
    verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)
    assert verdict == WatchdogVerdict.FIRE, f"expected FIRE at t=60; got {verdict}"
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET, (
        f"expected NO_PROGRESS_QUIET; got {watchdog.last_fire_reason}"
    )


def test_zero_activity_during_no_output_at_start_window_fires() -> None:
    """Zero activity during the no_output_at_start window MUST fire.

    With no recorded activity of any kind and no corroborated live child, the
    first evaluate at or past ``no_output_at_start_seconds`` (30s) returns
    FIRE with ``last_fire_reason == NO_OUTPUT_AT_START``.
    """
    watchdog, clock = _make_watchdog()
    watchdog.record_invocation_start()

    clock.advance(_NO_OUTPUT_AT_START_SECONDS)
    verdict = watchdog.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"expected FIRE at no_output_at_start threshold; got {verdict}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START, (
        f"expected NO_OUTPUT_AT_START; got {watchdog.last_fire_reason}"
    )
