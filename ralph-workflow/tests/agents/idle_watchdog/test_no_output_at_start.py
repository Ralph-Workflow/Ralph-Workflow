"""NO_OUTPUT_AT_START deferral tests for subagent progress within window.

The watchdog fires ``NO_OUTPUT_AT_START`` when an agent has been alive for
``no_output_at_start_seconds`` with zero recorded activity (no stdout, no
tool call, no file change, no subagent output).  This is the smart-kill
that catches agents that never start (e.g. dispatching a subagent and
immediately going silent).

The fix for the false-positive on dispatched subagents: feed ALL parsers
(via ``ParserTemplateBase.emit_subagent_activity``) into the
``invoke_subagent_sink`` contextvar so the per-run watchdog's
``record_subagent_work`` channel sees the per-tool subagent progress
within the ``activity_evidence_ttl_seconds`` window.  The watchdog's
existing ``_channel_evidence_active`` deferral gate already returns
``CONTINUE`` for fresh subagent activity (no production change needed).

This test pins the deferral behavior end-to-end:

  - ``test_defers_when_subagent_progress_observed_within_window``:
    record_subagent_work at 30s -> evaluate() returns CONTINUE.
  - ``test_does_not_defer_after_evidence_ttl_expired``: same setup,
    advance past the TTL -> evaluate() returns FIRE with reason
    NO_OUTPUT_AT_START.

Both tests use FakeClock so the wall-clock advance is deterministic.
No real subprocess, no real sleep, no real network.
"""

from __future__ import annotations

from dataclasses import dataclass

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.process.monitor import (
    ProcessMonitor,
    SubagentOutputCapture,
)

_NO_OUTPUT_AT_START_SECONDS = 30.0
_ACTIVITY_TTL_SECONDS = 180.0
_MAX_WAITING_SECONDS = 600.0


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


@dataclass
class _NoProcessMonitor(ProcessMonitor):
    """Fake process monitor: no live subagents, no captures."""

    live_count: int = 0
    classified: tuple = ()

    def live_subagent_count(self) -> int:
        return self.live_count

    def classified_processes(self) -> tuple:
        return self.classified

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return {}


def _make_policy(
    *,
    no_output_at_start: float = _NO_OUTPUT_AT_START_SECONDS,
    activity_ttl: float = _ACTIVITY_TTL_SECONDS,
) -> TimeoutPolicy:
    return TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=no_output_at_start,
        no_progress_quiet_seconds=None,
        max_waiting_on_child_seconds=_MAX_WAITING_SECONDS,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        activity_evidence_ttl_seconds=activity_ttl,
    )


def _make_watchdog(
    *,
    start: float = 0.0,
    process_monitor: ProcessMonitor | None = None,
    activity_ttl: float = _ACTIVITY_TTL_SECONDS,
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=start)
    return (
        IdleWatchdog(
            _make_policy(activity_ttl=activity_ttl),
            clock,
            process_monitor=process_monitor or _NoProcessMonitor(),
        ),
        clock,
    )


def test_defers_when_subagent_progress_observed_within_window() -> None:
    """NO_OUTPUT_AT_START defers (returns CONTINUE) when subagent progress
    is observed within ``activity_evidence_ttl_seconds``.

    This is the central test for the false-positive fix: a recently-launched
    agent that dispatches a subagent and immediately goes silent must NOT
    be killed at 30s.  The upstream subagent sink feed (via
    ``record_subagent_work``) keeps the per-channel evidence fresh so the
    ``_channel_evidence_active`` deferral gate returns CONTINUE.
    """
    wd, clock = _make_watchdog()
    wd.record_invocation_start()

    # Advance the clock past the no_output_at_start threshold (30s).
    clock.advance(31.0)
    # But BEFORE evaluate, record a fresh subagent signal.  This mimics
    # the new emit_subagent_activity hook in stream_parsed_agent_activity
    # feeding the watchdog sink via the contextvar.
    wd.record_subagent_work(description="tool_use:Bash")
    verdict = wd.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"NO_OUTPUT_AT_START MUST defer when subagent progress is fresh; got {verdict}"
    )


def test_does_not_defer_after_evidence_ttl_expired() -> None:
    """NO_OUTPUT_AT_START fires normally after the activity_evidence_ttl
    window expires.  The deferral gate is bounded by the TTL so a subagent
    that dispatched but went silent for the full TTL is NOT evidence of
    progress and the watchdog returns to the normal fire path.
    """
    wd, clock = _make_watchdog()
    wd.record_invocation_start()

    # Record subagent progress at 30s (within no_output_at_start window).
    clock.advance(31.0)
    wd.record_subagent_work(description="tool_use:Bash")
    # Subagent progress is now stale after TTL expires.
    clock.advance(_ACTIVITY_TTL_SECONDS + 1.0)
    verdict = wd.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"NO_OUTPUT_AT_START MUST fire after the activity TTL expires; got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START
