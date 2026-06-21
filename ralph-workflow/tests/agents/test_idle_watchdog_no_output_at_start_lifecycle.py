"""Test that a LIFECYCLE frame doesn't bypass the NO_OUTPUT_AT_START trip.

Also covers live-corroboration NO_OUTPUT_AT_START deferral:
  - The watchdog MUST consult the LIVE self._safe_corroborate() call inside
    _evaluate_no_output_at_start, not the stale self._last_alive_by field
    (which is only populated post-fire by NO_PROGRESS_QUIET at line 620 of
    idle_watchdog.py).
  - The watchdog MUST defer NO_OUTPUT_AT_START when the LIVE corroborator
    reports a CorroborationSnapshot with alive_by != None, even after 60s of
    zero meaningful output.
  - The watchdog MUST defer NO_OUTPUT_AT_START when
    cumulative_waiting_on_child_seconds > 0.0, because an agent that
    survived one waiting run has demonstrated it is alive.
  - The watchdog MUST still FIRE NO_OUTPUT_AT_START when the corroborator
    returns an empty snapshot AND no prior waiting run exists -- the
    no-false-positive contract.
"""

from __future__ import annotations

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    CorroborationSnapshot,
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
from ralph.agents.timeout_clock import FakeClock
from ralph.process.child_liveness import AliveBy


class TestNoOutputAtStartLifecycleBypass:
    """Test reproducing the bug where a lifecycle frame bypasses NO_OUTPUT_AT_START."""

    def test_lifecycle_activity_does_not_bypass_no_output_at_start(self) -> None:
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=30.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)
        watchdog = IdleWatchdog(config, clock)

        watchdog.record_invocation_start()

        # Advance the clock slightly to simulate some startup delay
        clock.advance(5.0)

        # Simulate a lifecycle activity line (which should NOT count as meaningful output)
        watchdog.record_lifecycle_activity()

        # Advance the clock past the 30.0s threshold since start
        clock.advance(26.0)

        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

        # On the buggy implementation, this assertion will fail because
        # verdict is WatchdogVerdict.WAITING_ON_CHILD, not WatchdogVerdict.FIRE.
        assert verdict == WatchdogVerdict.FIRE
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START


class TestChannelEvidenceDeferNoOutputAtStart:
    """Proves that waiting-evidence channels suppress NO_OUTPUT_AT_START."""

    def test_subagent_work_progress_defers_no_output_at_start(self) -> None:
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=30.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
            activity_evidence_ttl_seconds=100.0,
        )
        clock = FakeClock(start=0.0)
        watchdog = IdleWatchdog(config, clock)

        watchdog.record_invocation_start()
        watchdog.record_subagent_work()

        clock.advance(31.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

        assert verdict == WatchdogVerdict.CONTINUE
        assert watchdog.last_fire_reason is None

    def test_workspace_event_progress_defers_no_output_at_start(self) -> None:
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=30.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
            activity_evidence_ttl_seconds=100.0,
        )
        clock = FakeClock(start=0.0)
        watchdog = IdleWatchdog(config, clock)

        watchdog.record_invocation_start()
        watchdog.record_workspace_event(kind=WorkspaceChangeKind.SOURCE)

        clock.advance(31.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

        assert verdict == WatchdogVerdict.CONTINUE
        assert watchdog.last_fire_reason is None

    def test_mcp_tool_call_progress_defers_no_output_at_start(self) -> None:
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=30.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
            activity_evidence_ttl_seconds=100.0,
        )
        clock = FakeClock(start=0.0)
        watchdog = IdleWatchdog(config, clock)

        watchdog.record_invocation_start()
        watchdog.record_mcp_tool_call()

        clock.advance(31.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

        assert verdict == WatchdogVerdict.CONTINUE
        assert watchdog.last_fire_reason is None


class TestNoOutputAtStartLiveCorroborationDefer:
    """Live-corroboration deferral for NO_OUTPUT_AT_START.

    The watchdog must call self._safe_corroborate() LIVE inside
    _evaluate_no_output_at_start (not read the stale self._last_alive_by
    field). When the LIVE snapshot reports alive_by != None, the
    NO_OUTPUT_AT_START fire is deferred.

    Three tests pin the contract:

    1. test_defers_no_output_at_start_when_live_corroborator_reports_alive_by:
       live corroborator returns a snapshot with alive_by=OS_DESCENDANT;
       assert CONTINUE and that the corroborator was invoked LIVE during
       evaluate (not the stale last_alive_by field).

    2. test_defers_no_output_at_start_when_cumulative_waiting_on_child_positive:
       drive one full WAITING_ON_CHILD cycle to accumulate waiting time,
       reset to ACTIVE state, advance past no_output_at_start_seconds with
       no new activity and no live corroborator alive_by; assert CONTINUE.

    3. test_still_fires_when_live_corroborator_returns_empty_and_no_waiting_run:
       corroborator returns empty CorroborationSnapshot(); no prior
       waiting run; advance past no_output_at_start_seconds; assert FIRE.
    """

    def test_defers_no_output_at_start_when_live_corroborator_reports_alive_by(self) -> None:
        """Live corroborator alive_by signal defers NO_OUTPUT_AT_START.

        The corroborator returns ``alive_by=OS_DESCENDANT`` (a live-child
        signal). The watchdog must defer NO_OUTPUT_AT_START because the
        LIVE corroborator confirms a live child agent. The prove-the-call
        assertion verifies that the corroborator was invoked LIVE during
        evaluate() (not via the stale ``self._last_alive_by`` field which
        is only populated post-fire by NO_PROGRESS_QUIET).

        Idle_timeout_seconds=300 (well past no_output_at_start_seconds=60)
        so the watchdog does NOT fire NO_OUTPUT_DEADLINE either -- the
        final verdict is CONTINUE because no_output_at_start deferred
        AND the agent is not past idle_timeout.
        """
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)

        call_count: list[int] = [0]

        def _live_corroborator() -> CorroborationSnapshot:
            call_count[0] += 1
            return CorroborationSnapshot(
                alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
                scoped_child_active=True,
                oldest_child_seconds=5.0,
            )

        watchdog = IdleWatchdog(config, clock, corroborator=_live_corroborator)

        watchdog.record_invocation_start()

        clock.advance(61.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict == WatchdogVerdict.CONTINUE, (
            f"expected CONTINUE (live corroborator reports alive_by=OS_DESCENDANT),"
            f" got {verdict}"
        )
        assert watchdog.last_fire_reason is None, (
            f"expected last_fire_reason=None (no fire happened),"
            f" got {watchdog.last_fire_reason}"
        )
        # Proves the LIVE call semantics: the corroborator was invoked
        # during evaluate(), not by reading the stale last_alive_by field.
        assert call_count[0] >= 1, (
            f"expected the live corroborator to be invoked at least once"
            f" during evaluate(), got {call_count[0]} invocations"
        )

    def test_defers_no_output_at_start_when_cumulative_waiting_on_child_positive(
        self,
    ) -> None:
        """Cumulative waiting time > 0 defers NO_OUTPUT_AT_START.

        Drive one full WAITING_ON_CHILD entry/exit cycle so
        cumulative_waiting_on_child_seconds > 0, reset to ACTIVE state
        (no live corroborator alive_by), then advance past
        no_output_at_start_seconds with no new activity. The watchdog
        must defer NO_OUTPUT_AT_START because the agent has already
        demonstrated it is alive by surviving a waiting run.

        Setup: idle_timeout_seconds=300 (well past the no_output_at_start
        threshold) so the watchdog does NOT fire NO_OUTPUT_DEADLINE
        before the no_output_at_start check. ``has_meaningful_output``
        stays False (no ``record_activity()`` call) so the NO_OUTPUT_AT_START
        check proceeds to the cumulative-waiting deferral gate.
        """
        config = TimeoutPolicy(
            idle_timeout_seconds=10.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)

        def _empty_corroborator() -> CorroborationSnapshot:
            return CorroborationSnapshot(
                alive_by=None,
                scoped_child_active=False,
                oldest_child_seconds=0.0,
            )

        watchdog = IdleWatchdog(config, clock, corroborator=_empty_corroborator)
        watchdog.record_invocation_start()

        # Drive a WAITING_ON_CHILD entry/exit cycle. Advance past the
        # idle deadline (10s) to enter WAITING_ON_CHILD on the first
        # evaluate; advance a bit more so the run accumulates waiting
        # time; then transition back to ACTIVE on the next evaluate to
        # accumulate the run into cumulative_waiting_on_child_seconds.
        # The NO_OUTPUT_AT_START check returns None during the cycle
        # itself because `(now - _last_meaningful_output_at) < 60s` at
        # clock=11 and clock=31, so the gate deferral is NOT exercised
        # during the cycle -- it is exercised AFTER the cycle when
        # `now - _last_meaningful_output_at >= 60s` AND
        # `cumulative_waiting_on_child_seconds > 0`.
        clock.advance(11.0)
        first_verdict = watchdog.evaluate(
            classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD
        )
        assert first_verdict == WatchdogVerdict.WAITING_ON_CHILD

        clock.advance(20.0)
        watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
        # The exit transitions out of WAITING_ON_CHILD; the cumulative
        # waiting time is now > 0.
        assert watchdog.cumulative_waiting_on_child_seconds > 0.0

        # Now drive past no_output_at_start_seconds (60s) with no new
        # activity and no live corroborator alive_by. The watchdog must
        # defer NO_OUTPUT_AT_START because cumulative_waiting_on_child_seconds
        # is positive. Idle_elapsed (92) is past idle_timeout (10) but
        # the deferral check returns None BEFORE the idle_timeout check,
        # so the watchdog does NOT fire NO_OUTPUT_AT_START -- it then
        # proceeds to fire NO_OUTPUT_DEADLINE (a different fire path),
        # which is the expected behavior. The KEY assertion is that
        # ``last_fire_reason != NO_OUTPUT_AT_START`` -- if the deferral
        # gate were broken, NO_OUTPUT_AT_START would have fired first.
        clock.advance(61.0)
        watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert watchdog.last_fire_reason != WatchdogFireReason.NO_OUTPUT_AT_START, (
            f"expected last_fire_reason != NO_OUTPUT_AT_START (NO_OUTPUT_AT_START"
            f" must defer when cumulative_waiting_on_child_seconds > 0),"
            f" got {watchdog.last_fire_reason}"
        )

    def test_still_fires_when_live_corroborator_returns_empty_and_no_waiting_run(
        self,
    ) -> None:
        """No false-positive deferral: corroborator empty AND no prior waiting run.

        When the corroborator returns an empty CorroborationSnapshot
        (alive_by=None) AND there is no prior waiting run (so
        cumulative_waiting_on_child_seconds == 0), the watchdog must
        still FIRE NO_OUTPUT_AT_START after the threshold elapses with
        no activity. This pins the no-false-positive contract.
        """
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)

        def _empty_corroborator() -> CorroborationSnapshot:
            return CorroborationSnapshot()

        watchdog = IdleWatchdog(config, clock, corroborator=_empty_corroborator)
        watchdog.record_invocation_start()

        # No waiting run accumulated; advance past no_output_at_start_seconds.
        clock.advance(61.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict == WatchdogVerdict.FIRE, (
            f"expected FIRE (corroborator empty, no prior waiting run),"
            f" got verdict={verdict}"
        )
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START, (
            f"expected last_fire_reason == NO_OUTPUT_AT_START, got"
            f" {watchdog.last_fire_reason}"
        )
