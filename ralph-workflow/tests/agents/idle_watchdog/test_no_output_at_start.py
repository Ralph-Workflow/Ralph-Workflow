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


def _waiting_on_child() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


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
    silent_subagent_seconds: float | None = None,
) -> TimeoutPolicy:
    return TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=no_output_at_start,
        no_progress_quiet_seconds=None,
        max_waiting_on_child_seconds=_MAX_WAITING_SECONDS,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        activity_evidence_ttl_seconds=activity_ttl,
        # Disable the SILENT_SUBAGENT diagnostic in this test file so
        # the assertions exercise the TTL-bounded deferral gate for
        # ``_channel_evidence_active`` rather than the SILENT_SUBAGENT
        # classifier branch.  The SILENT_SUBAGENT path is covered in
        # ``tests/agents/idle_watchdog/test_silent_subagent_runtime.py``
        # with its own runtime contract tests.
        silent_subagent_seconds=silent_subagent_seconds,
    )


def _make_watchdog(
    *,
    start: float = 0.0,
    process_monitor: ProcessMonitor | None = None,
    activity_ttl: float = _ACTIVITY_TTL_SECONDS,
    silent_subagent_seconds: float | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=start)
    return (
        IdleWatchdog(
            _make_policy(
                activity_ttl=activity_ttl,
                silent_subagent_seconds=silent_subagent_seconds,
            ),
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


def test_no_output_at_start_defers_on_first_waiting_on_child_entry() -> None:
    """NO_OUTPUT_AT_START defers on the FIRST WAITING_ON_CHILD entry.

    This is the exact prompt scenario: a subagent is dispatched at
    invocation start, the agent transitions to WAITING_ON_CHILD, and the
    watchdog polls at 30s.  The cumulative waiting-on-child time is still
    0.0 on the first entry, so the old cumulative gate could not defer;
    the new classify_quiet WAITING_ON_CHILD early-exit must defer instead.
    """
    wd, clock = _make_watchdog()
    wd.record_invocation_start()

    clock.advance(31.0)
    verdict = wd.evaluate(classify_quiet=_waiting_on_child)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"NO_OUTPUT_AT_START MUST defer on first WAITING_ON_CHILD entry; got {verdict}"
    )
    assert wd.last_fire_reason is None


def test_no_output_at_start_fires_after_waiting_ceiling_reached() -> None:
    """After the WAITING_ON_CHILD cumulative ceiling is reached, the
    watchdog fires CHILDREN_PERSIST_TOO_LONG -- NOT NO_OUTPUT_AT_START.

    The WAITING_ON_CHILD early-exit in _evaluate_no_output_at_start only
    defers the 30s short kill; the 600s cumulative ceiling inside
    _handle_waiting_branch remains the upper bound for live-child stalls.
    """
    wd, clock = _make_watchdog()
    wd.record_invocation_start()

    # Enter WAITING_ON_CHILD and advance past the 600s ceiling.
    # We deliberately provide no channel evidence and no corroborator so
    # the only deferral path is the new WAITING_ON_CHILD early-exit; once
    # the cumulative ceiling is reached, CHILDREN_PERSIST_TOO_LONG fires.
    # First cross the idle_timeout so _evaluate_final_verdict enters the
    # WAITING_ON_CHILD branch and starts the waiting run; then advance the
    # remainder of the 600s ceiling.
    clock.advance(61.0)
    wd.evaluate(classify_quiet=_waiting_on_child)
    clock.advance(_MAX_WAITING_SECONDS)
    verdict = wd.evaluate(classify_quiet=_waiting_on_child)
    assert verdict == WatchdogVerdict.FIRE, (
        f"expected FIRE after waiting ceiling reached; got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG, (
        f"expected CHILDREN_PERSIST_TOO_LONG at ceiling, got {wd.last_fire_reason}"
    )


def test_no_output_at_start_fires_at_threshold_even_when_floor_unreached() -> None:
    """NO_OUTPUT_AT_START fires at the threshold even when invocation_elapsed
    is under the ``no_progress_quiet_minimum_invocation_seconds`` floor.

    The dumb-kill floor is intentionally NOT consulted inside
    ``_evaluate_no_output_at_start`` so the operator's
    ``no_output_at_start_seconds`` short ceiling is the single source of
    truth for ``NO_OUTPUT_AT_START`` lifetime. The floor is enforced
    inside ``_is_no_progress_quiet`` for ``NO_PROGRESS_QUIET`` only.
    """
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        no_progress_quiet_minimum_invocation_seconds=120.0,
        max_waiting_on_child_seconds=_MAX_WAITING_SECONDS,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        activity_evidence_ttl_seconds=_ACTIVITY_TTL_SECONDS,
        silent_subagent_seconds=None,
    )
    wd = IdleWatchdog(
        policy,
        clock,
        process_monitor=_NoProcessMonitor(),
    )
    wd.record_invocation_start()
    # Advance to 60s: past the 30s NO_OUTPUT_AT_START threshold AND
    # under the 120s dumb-kill floor. The watchdog MUST fire at the
    # short ceiling; the floor does NOT defer NO_OUTPUT_AT_START.
    clock.advance(60.0)
    verdict = wd.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"NO_OUTPUT_AT_START MUST fire at the threshold regardless of"
        f" the dumb-kill floor (invocation_elapsed=60s, threshold=30s,"
        f" floor=120s); got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START
