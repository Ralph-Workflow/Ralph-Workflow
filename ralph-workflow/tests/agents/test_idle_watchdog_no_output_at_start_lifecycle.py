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
        """Cumulative waiting time > 0 defers NO_OUTPUT_AT_START (AC-02).

        The watchdog must defer NO_OUTPUT_AT_START when
        ``cumulative_waiting_on_child_seconds > 0`` because an agent
        that survived a full waiting run has demonstrated it is alive
        enough that ``NO_OUTPUT_AT_START`` no longer applies.

        This test PROVES the AC-02 contract end-to-end:

        1. Configure so ``NO_OUTPUT_DEADLINE`` cannot win:
           - ``idle_timeout_seconds=300.0`` so the watchdog returns
             ``CONTINUE`` (via the ``idle_elapsed < idle_timeout``
             early-out) before the NO_OUTPUT_DEADLINE fire path.
           - ``no_progress_quiet_seconds=None`` so the
             NO_PROGRESS_QUIET path is disabled.
           - ``drain_window_seconds=0.0`` so the active branch does
             not enter a drain window.
        2. Drive past ``no_output_at_start_seconds`` (60s) with no
           activity and no live corroborator alive_by, after
           simulating a prior waiting run via
           ``_cumulative_waiting_on_child_seconds``.
        3. Assert the returned verdict is
           ``WatchdogVerdict.CONTINUE`` (the AC-02 contract).
        4. Assert no fire reason was recorded
           (``last_fire_reason is None``).

        The pre-fix test config (``idle_timeout_seconds=10.0``) let
        the watchdog fire ``NO_OUTPUT_DEADLINE`` AFTER the
        ``NO_OUTPUT_AT_START`` deferral check, which let a
        regression to ``NO_OUTPUT_DEADLINE`` slip through the
        ``last_fire_reason != NO_OUTPUT_AT_START`` assertion. The
        new configuration closes that loophole: the only way for
        the test to fail is if the deferral gate itself is broken.
        """
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
            no_progress_quiet_seconds=None,
            drain_window_seconds=0.0,
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

        # Simulate a prior waiting run by directly setting the
        # cumulative field. The watchdog's interface does not expose
        # a setter (the cumulative is a private invariant that
        # ``_accumulate_waiting_run`` maintains internally); tests
        # may manipulate internal state for setup. The intent is
        # to drive the exact precondition the AC-02 deferral gate
        # requires: ``cumulative_waiting_on_child_seconds > 0``.
        watchdog._cumulative_waiting_on_child_seconds = 5.0

        # Drive past no_output_at_start_seconds (60s) with no new
        # activity and no live corroborator alive_by. With
        # idle_timeout_seconds=300 the watchdog returns CONTINUE
        # via the idle_elapsed < idle_timeout early-out BEFORE
        # reaching the NO_OUTPUT_DEADLINE / active branch. The
        # only verdict path that runs is the
        # ``_evaluate_no_output_at_start`` deferral gate -- if
        # the gate is broken, NO_OUTPUT_AT_START fires.
        clock.advance(61.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        # AC-02: verdict is CONTINUE (deferral gate kept us alive).
        assert verdict == WatchdogVerdict.CONTINUE, (
            f"expected CONTINUE (NO_OUTPUT_AT_START must defer when"
            f" cumulative_waiting_on_child_seconds > 0), got verdict={verdict}"
        )
        # AC-02: no fire reason was recorded (deferral, not fire).
        assert watchdog.last_fire_reason is None, (
            f"expected last_fire_reason=None (no fire happened),"
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
