"""Regression tests for the silent-after-tool-call false-positive wedge.

A single in-process MCP ``tools/call`` followed by quiet must NOT trigger a
premature ``NO_OUTPUT_AT_START`` or ``NO_PROGRESS_QUIET`` kill when the
corroborator still reports fresh child progress.  Conversely, when the
corroborator is stale and the subagent channel has gone silent past
``silent_subagent_seconds``, the smart-verdict gate MUST defer via
``StuckKind.SILENT_SUBAGENT`` rather than blindly firing.

All tests use FakeClock and injected corroborators; no real subprocess, no
real sleep, no real network.
"""

from __future__ import annotations

from dataclasses import dataclass

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    AliveBy,
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
_ACTIVITY_EVIDENCE_TTL_SECONDS = 300.0


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


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _make_watchdog(
    *,
    corroborator: object,
    activity_evidence_ttl: float = _ACTIVITY_EVIDENCE_TTL_SECONDS,
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=_IDLE_TIMEOUT_SECONDS,
        no_output_at_start_seconds=_NO_OUTPUT_AT_START_SECONDS,
        no_progress_quiet_seconds=_NO_PROGRESS_QUIET_SECONDS,
        no_progress_quiet_minimum_invocation_seconds=None,
        no_progress_quiet_heartbeat_ceiling_seconds=None,
        activity_evidence_ttl_seconds=activity_evidence_ttl,
        silent_subagent_seconds=_SILENT_SUBAGENT_SECONDS,
    )
    return (
        IdleWatchdog(
            policy,
            clock,
            process_monitor=_NoProcessMonitor(),
            corroborator=corroborator,
        ),
        clock,
    )


def test_single_mcp_tool_call_then_quiet_with_fresh_corroborator_does_not_fire() -> None:
    """Single MCP tool-call + quiet with fresh corroborator must NOT fire.

    The MCP tool-call keeps the ``mcp_tool`` first-party channel fresh within
    ``activity_evidence_ttl_seconds`` and the corroborator reports
    ``AliveBy.FRESH_PROGRESS``.  The watchdog must return CONTINUE at every
    poll through ``silent_subagent_seconds`` and must never set
    ``last_fire_reason``.
    """

    def _fresh_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            mcp_tool_call_count=1,
            last_mcp_tool_call_at=0.0,
        )

    watchdog, clock = _make_watchdog(corroborator=_fresh_corroborator)
    watchdog.record_invocation_start()
    watchdog.record_mcp_tool_call()

    for elapsed in (30.0, 60.0, 120.0, 180.0):
        clock.advance(elapsed - clock.monotonic())
        verdict = watchdog.evaluate(classify_quiet=_active)
        assert verdict == WatchdogVerdict.CONTINUE, (
            f"expected CONTINUE at t={elapsed}; got {verdict}"
        )

    assert watchdog.last_fire_reason is None


def test_subagent_silence_with_stale_corroborator_fires() -> None:
    """Stale subagent evidence + STALE corroborator MUST fire, not defer.

    We record one subagent progress observation and one MCP tool-call at
    t=0, then let both channels go stale. The corroborator reports
    ``OS_DESCENDANT_ONLY_STALE_PROGRESS``: a child process still EXISTS but
    has made no progress. That is a wedge, not work.

    This test previously asserted CONTINUE -- it encoded the production hang
    as correct behavior. A stale alive-by is explicitly NOT a deferring
    signal (``can_defer=False``; see ``_subagent_liveness_fresh``), and with
    no no-progress ceiling configured the run deferred forever. The gate MUST
    fire. A genuinely working child keeps the liveness channel fresh with
    ``can_defer=True`` and defers earlier via LOADING (branch 4).
    """

    def _stale_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
        )

    watchdog, clock = _make_watchdog(
        corroborator=_stale_corroborator,
        activity_evidence_ttl=60.0,
    )
    watchdog.record_invocation_start()
    watchdog.record_mcp_tool_call()
    watchdog.record_subagent_work(description="tool_use:Read")

    clock.advance(240.0)
    verdict = watchdog.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"expected FIRE at t=240 (stale child, silent parent); got {verdict}"
    )
    assert watchdog.last_fire_reason != WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER, (
        "a stale-progress child must not defer the fire indefinitely"
    )
