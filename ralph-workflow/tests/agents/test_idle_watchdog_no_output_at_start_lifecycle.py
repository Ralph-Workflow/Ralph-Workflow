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

        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        # Lifecycle activity must NOT count as meaningful output, so
        # NO_OUTPUT_AT_START still fires at the threshold when the agent
        # is ACTIVE.  (When the execution strategy reports WAITING_ON_CHILD,
        # the separate WAITING_ON_CHILD early-exit defers instead -- see
        # tests/agents/idle_watchdog/test_no_output_at_start.py.)
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

        The corroborator returns ``alive_by=FRESH_PROGRESS`` (a fresh
        live-child signal). The watchdog must defer NO_OUTPUT_AT_START
        because the LIVE corroborator confirms a live child agent. The
        prove-the-call assertion verifies that the corroborator was
        invoked LIVE during evaluate() (not via the stale
        ``self._last_alive_by`` field which is only populated
        post-fire by NO_PROGRESS_QUIET).

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
                alive_by=AliveBy.FRESH_PROGRESS,
                scoped_child_active=True,
                oldest_child_seconds=5.0,
            )

        watchdog = IdleWatchdog(config, clock, corroborator=_live_corroborator)

        watchdog.record_invocation_start()

        clock.advance(61.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict == WatchdogVerdict.CONTINUE, (
            f"expected CONTINUE (live corroborator reports alive_by=FRESH_PROGRESS), got {verdict}"
        )
        assert watchdog.last_fire_reason is None, (
            f"expected last_fire_reason=None (no fire happened), got {watchdog.last_fire_reason}"
        )
        # Proves the LIVE call semantics: the corroborator was invoked
        # during evaluate(), not by reading the stale last_alive_by field.
        assert call_count[0] >= 1, (
            f"expected the live corroborator to be invoked at least once"
            f" during evaluate(), got {call_count[0]} invocations"
        )

    def test_stale_alive_by_does_not_defer_no_output_at_start(self) -> None:
        """Stale ``AliveBy`` states do NOT defer NO_OUTPUT_AT_START.

        The watchdog must distinguish FRESH corroboration evidence
        (``FRESH_PROGRESS``, ``FRESH_HEARTBEAT_ONLY`` -- a child that
        has produced recent progress / heartbeat signal) from STALE
        evidence (``OS_DESCENDANT_ONLY_STALE_PROGRESS``,
        ``CPU_IDLE_WHILE_ALIVE``, ``LOG_STALE_WHILE_ALIVE``,
        ``STALE_LABEL_ONLY`` -- a child that has stopped producing
        fresh evidence). Only fresh states defer the short
        NO_OUTPUT_AT_START kill; stale evidence falls through to
        ``_gate_fire`` so the StuckClassifier sees the live snapshot
        and the short kill still applies.

        Pre-fix, the deferral gate was ``corroboration.alive_by is
        not None``, which deferred on every AliveBy value including
        stale states. A wedged startup that reported
        ``OS_DESCENDANT_ONLY_STALE_PROGRESS`` would defer the short
        kill and never reach ``_gate_fire`` / StuckClassifier. The
        post-fix gate is ``_alive_by_is_fresh(...)`` which returns
        True ONLY for ``FRESH_PROGRESS`` and ``FRESH_HEARTBEAT_ONLY``.
        """
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)

        def _stale_corroborator() -> CorroborationSnapshot:
            return CorroborationSnapshot(
                alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
                scoped_child_active=True,
                oldest_child_seconds=5.0,
            )

        watchdog = IdleWatchdog(config, clock, corroborator=_stale_corroborator)
        watchdog.record_invocation_start()

        # Advance past the short NO_OUTPUT_AT_START threshold (60s).
        clock.advance(61.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        # Stale AliveBy MUST NOT defer -- the short kill fires.
        assert verdict == WatchdogVerdict.FIRE, (
            f"expected FIRE (stale AliveBy MUST NOT defer"
            f" NO_OUTPUT_AT_START), got verdict={verdict}"
        )
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START, (
            f"expected last_fire_reason == NO_OUTPUT_AT_START, got {watchdog.last_fire_reason}"
        )

    def test_cpu_idle_while_alive_does_not_defer_no_output_at_start(self) -> None:
        """Stale ``AliveBy.CPU_IDLE_WHILE_ALIVE`` does NOT defer NO_OUTPUT_AT_START.

        Mirrors ``test_stale_alive_by_does_not_defer_no_output_at_start``
        for a different stale AliveBy value. The wedged-startup pattern
        applies: the descendant process is alive in the OS process
        tree but has not used CPU recently -- the process is hung
        and NO_OUTPUT_AT_START MUST still fire.
        """
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)

        def _cpu_idle_corroborator() -> CorroborationSnapshot:
            return CorroborationSnapshot(
                alive_by=AliveBy.CPU_IDLE_WHILE_ALIVE,
                scoped_child_active=True,
                oldest_child_seconds=5.0,
            )

        watchdog = IdleWatchdog(config, clock, corroborator=_cpu_idle_corroborator)
        watchdog.record_invocation_start()
        clock.advance(61.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict == WatchdogVerdict.FIRE, (
            f"expected FIRE (stale AliveBy.CPU_IDLE_WHILE_ALIVE MUST NOT defer),"
            f" got verdict={verdict}"
        )
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START

    def test_log_stale_while_alive_does_not_defer_no_output_at_start(self) -> None:
        """Stale ``AliveBy.LOG_STALE_WHILE_ALIVE`` does NOT defer NO_OUTPUT_AT_START."""
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)

        def _log_stale_corroborator() -> CorroborationSnapshot:
            return CorroborationSnapshot(
                alive_by=AliveBy.LOG_STALE_WHILE_ALIVE,
                scoped_child_active=True,
                oldest_child_seconds=5.0,
            )

        watchdog = IdleWatchdog(config, clock, corroborator=_log_stale_corroborator)
        watchdog.record_invocation_start()
        clock.advance(61.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict == WatchdogVerdict.FIRE, (
            f"expected FIRE (stale AliveBy.LOG_STALE_WHILE_ALIVE MUST NOT defer),"
            f" got verdict={verdict}"
        )
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START

    def test_stale_label_only_does_not_defer_no_output_at_start(self) -> None:
        """Stale ``AliveBy.STALE_LABEL_ONLY`` does NOT defer NO_OUTPUT_AT_START."""
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)

        def _stale_label_corroborator() -> CorroborationSnapshot:
            return CorroborationSnapshot(
                alive_by=AliveBy.STALE_LABEL_ONLY,
                scoped_child_active=True,
                oldest_child_seconds=5.0,
            )

        watchdog = IdleWatchdog(config, clock, corroborator=_stale_label_corroborator)
        watchdog.record_invocation_start()
        clock.advance(61.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict == WatchdogVerdict.FIRE, (
            f"expected FIRE (stale AliveBy.STALE_LABEL_ONLY MUST NOT defer), got verdict={verdict}"
        )
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START

    def test_defers_no_output_at_start_when_cumulative_waiting_on_child_positive(
        self,
    ) -> None:
        """Cumulative waiting time > 0 defers NO_OUTPUT_AT_START (AC-02).

        The watchdog must defer NO_OUTPUT_AT_START when
        ``cumulative_waiting_on_child_seconds > 0`` because an agent
        that survived a full waiting run has demonstrated it is alive
        enough that ``NO_OUTPUT_AT_START`` no longer applies.

        This test PROVES the AC-02 contract end-to-end via observable
        behavior (no private-field mutation):

        1. Configure so the cycle and the assertion both work:
           - ``no_output_at_start_seconds=200.0`` is larger than the
             waiting-cycle duration (101s..106s) but smaller than the
             post-cycle advance (200s). This ordering lets the cycle
             run BEFORE ``_evaluate_no_output_at_start`` becomes
             eligible -- a control test (with no prior cycle) would
             FIRE ``NO_OUTPUT_AT_START`` at t=200s and the test would
             correctly fail the CONTINUE assertion.
           - ``idle_timeout_seconds=100.0`` is small enough that the
             waiting branch is reachable at t=101s (the cycle must
             happen PAST idle_timeout because the waiting branch is
             only entered when idle_elapsed >= idle_timeout).
           - ``drain_window_seconds=300.0`` is large enough that
             after the cycle the watchdog is inside the drain
             window (so the post-cycle verdict is ``CONTINUE``
             rather than ``NO_OUTPUT_DEADLINE``), letting us assert
             the deferral against a stable CONTINUE signal.
           - ``no_progress_quiet_seconds=None`` so the
             NO_PROGRESS_QUIET path is disabled.
        2. Drive ONE full ``WAITING_ON_CHILD`` entry/exit cycle
           through the public ``evaluate(classify_quiet=...)`` API
           so the ``_cumulative_waiting_on_child_seconds`` invariant
           is earned via observable behavior (the public
           ``cumulative_waiting_on_child_seconds`` property reads
           > 0 after the cycle).
        3. Advance past ``no_output_at_start_seconds`` (200s) with
           no new activity and no live corroborator alive_by. The
           only verdict path that runs is the
           ``_evaluate_no_output_at_start`` deferral gate -- if
           the gate is broken, NO_OUTPUT_AT_START fires first
           (BEFORE the drain-window CONTINUE) and the watchdog
           returns ``FIRE`` with ``last_fire_reason ==
           NO_OUTPUT_AT_START``. The test would then fail the
           CONTINUE + last_fire_reason-is-None assertions below.
        4. Assert the returned verdict is
           ``WatchdogVerdict.CONTINUE`` (the AC-02 contract).
        5. Assert no fire reason was recorded
           (``last_fire_reason is None``).

        The pre-fix test directly mutated the private
        ``_cumulative_waiting_on_child_seconds`` field, which is
        implementation-detail coupling (and per the repository's
        black-box test policy is a smell). The post-fix test
        drives the invariant through the public
        ``evaluate(classify_quiet=...)`` interface and reads the
        cumulative via the public ``cumulative_waiting_on_child_seconds``
        property so the AC-02 contract is fully observable.
        """
        config = TimeoutPolicy(
            idle_timeout_seconds=100.0,
            no_output_at_start_seconds=200.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
            no_progress_quiet_seconds=None,
            drain_window_seconds=300.0,
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

        # (a) Advance past the idle deadline (t=101) so the next
        # evaluate enters the WAITING_ON_CHILD branch (the branch
        # is only reachable when idle_elapsed >= idle_timeout_seconds).
        # At t=101, the no_output_at_start check sees
        # (101 - 0) < 200, so it returns None BEFORE the
        # active/waiting branch selection.
        clock.advance(101.0)
        # (b) Enter the WAITING_ON_CHILD branch via the public
        # API: classify_quiet returns WAITING_ON_CHILD and
        # _handle_waiting_branch starts the run. This is the
        # public entry point -- the watchdog starts tracking the
        # waiting run internally without any private-field
        # manipulation.
        result = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
        assert result == WatchdogVerdict.WAITING_ON_CHILD, (
            f"expected WAITING_ON_CHILD (entering the waiting branch), got {result}"
        )

        # (c) Advance 5s in the waiting state.
        clock.advance(5.0)
        # (d) Exit the WAITING_ON_CHILD branch via the public
        # API: classify_quiet returns ACTIVE and _handle_active_branch
        # accumulates the elapsed waiting time into
        # _cumulative_waiting_on_child_seconds. The branch then
        # enters the drain window (drain_window_seconds=300) and
        # returns CONTINUE.
        result = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
        assert result == WatchdogVerdict.CONTINUE, (
            f"expected CONTINUE (drain window entered after cycle), got {result}"
        )

        # (e) Public-property observation: the cumulative is now
        # > 0 because the cycle ran through the public API. This
        # is the AC-02 precondition earned via observable
        # behavior, NOT via private-field mutation.
        assert watchdog.cumulative_waiting_on_child_seconds > 0.0, (
            f"expected cumulative_waiting_on_child_seconds > 0 after the cycle,"
            f" got {watchdog.cumulative_waiting_on_child_seconds}"
        )

        # (f) Advance past no_output_at_start_seconds=200.0 so
        # the next evaluate triggers the no_output_at_start check.
        # The watchdog is inside the drain window
        # (drain_started_at = 106.0; drain_window_seconds = 300.0;
        # current_time = 201.0; drain_elapsed = 95.0 < 300).
        clock.advance(95.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        # AC-02: verdict is CONTINUE. The verdict path is:
        #   1. _evaluate_no_output_at_start -- (201.0 - 0) = 201.0
        #      >= 200 -> trigger eligible; live corroborator empty
        #      (alive_by=None, not fresh) -> deferral candidate;
        #      cumulative > 0 -> DEFER (returns None).
        #   2. idle_timeout check: 201.0 > 100 -> past idle.
        #   3. _evaluate_final_verdict -> in drain_window ->
        #      _handle_drain_window -> drain_elapsed=95.0 < 300 ->
        #      CONTINUE.
        # If the deferral gate were broken, step 1 would FIRE
        # NO_OUTPUT_AT_START and we would see FIRE, not CONTINUE.
        assert verdict == WatchdogVerdict.CONTINUE, (
            f"expected CONTINUE (NO_OUTPUT_AT_START must defer when"
            f" cumulative_waiting_on_child_seconds > 0), got verdict={verdict}"
        )
        # AC-02: no fire reason was recorded (deferral, not fire).
        assert watchdog.last_fire_reason is None, (
            f"expected last_fire_reason=None (no fire happened), got {watchdog.last_fire_reason}"
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
            f"expected FIRE (corroborator empty, no prior waiting run), got verdict={verdict}"
        )
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START, (
            f"expected last_fire_reason == NO_OUTPUT_AT_START, got {watchdog.last_fire_reason}"
        )


class TestSafeCorroborateFailsClosed:
    """Regression: ``_safe_corroborate`` MUST normalize a ``None`` (or any
    non-``CorroborationSnapshot``) return to an empty
    ``CorroborationSnapshot`` so callers like ``_evaluate_no_output_at_start``
    can safely read ``corroboration.alive_by`` without an ``AttributeError``.

    Pre-fix, ``_safe_corroborate`` returned ``self._corroborator()``
    directly. When the corroborator returned ``None``, callers that read
    ``corroboration.alive_by`` crashed mid-evaluation instead of
    failing closed to a no-defer signal. The watchdog is supposed to
    fail closed (empty snapshot = "no live evidence" = conservative
    no-defer), so the empty-snapshot normalization is the correct
    fail-closed behavior.

    These tests cover three contract paths:
      1. ``corroborator=lambda: None`` returns ``None`` -> watchdog
         evaluation continues safely and fires NO_OUTPUT_AT_START
         (empty corroboration means no live evidence -> no defer).
      2. ``corroborator=lambda: "not a snapshot"`` returns a non-snapshot
         value -> normalized to empty, watchdog continues safely.
      3. ``_safe_corroborate`` directly returns a
         ``CorroborationSnapshot`` even when the corroborator returns
         ``None`` (unit-level assertion of the normalization).
    """

    def test_safe_corroborate_normalizes_none_return_to_empty_snapshot(self) -> None:
        """A corroborator returning ``None`` is normalized to an empty
        ``CorroborationSnapshot`` so callers never see a ``None`` snapshot.
        """
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)

        # Corroborator that returns None (the bug case).
        watchdog = IdleWatchdog(config, clock, corroborator=lambda: None)
        watchdog.record_invocation_start()

        snapshot = watchdog._safe_corroborate()
        assert snapshot is not None, "_safe_corroborate MUST normalize None to an empty snapshot"
        assert isinstance(snapshot, CorroborationSnapshot)
        assert snapshot.alive_by is None
        # scoped_child_active is None by default (Optional[bool]); the
        # important property is "no live evidence" which both None and
        # False satisfy. Falsy check pins the conservative no-defer
        # signal without coupling to the default representation.
        assert not snapshot.scoped_child_active

    def test_safe_corroborate_normalizes_non_snapshot_return_to_empty_snapshot(
        self,
    ) -> None:
        """A corroborator returning any non-``CorroborationSnapshot`` value
        (e.g. a plain string, dict, int) is normalized to an empty snapshot.
        """
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)

        for bogus_value in ("not a snapshot", 42, {"alive_by": "OS_DESCENDANT"}, []):
            watchdog = IdleWatchdog(config, clock, corroborator=lambda value=bogus_value: value)
            snapshot = watchdog._safe_corroborate()
            assert isinstance(snapshot, CorroborationSnapshot), (
                f"non-snapshot return {bogus_value!r} MUST normalize to empty"
                f" CorroborationSnapshot, got {snapshot!r}"
            )
            assert snapshot.alive_by is None
            assert not snapshot.scoped_child_active

    def test_watchdog_evaluate_continues_safely_when_corroborator_returns_none(
        self,
    ) -> None:
        """Watchdog evaluation does NOT crash when the corroborator returns
        ``None``. With no live evidence and no prior waiting run, the
        watchdog fires NO_OUTPUT_AT_START (the no-false-positive contract
        is preserved because empty corroboration = "no live evidence" =
        conservative no-defer).
        """
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)

        watchdog = IdleWatchdog(config, clock, corroborator=lambda: None)
        watchdog.record_invocation_start()

        # Drive past no_output_at_start_seconds with no activity. With
        # idle_timeout_seconds=300 the watchdog reaches the
        # _evaluate_no_output_at_start path (no idle_elapsed early-out).
        # Pre-fix this raised AttributeError because corroboration was None
        # and _evaluate_no_output_at_start read corroboration.alive_by.
        clock.advance(61.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        # No live evidence + no prior waiting run => NO_OUTPUT_AT_START fires.
        assert verdict == WatchdogVerdict.FIRE, (
            f"expected FIRE (no live evidence, no prior waiting run), got verdict={verdict}"
        )
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START, (
            f"expected last_fire_reason == NO_OUTPUT_AT_START, got {watchdog.last_fire_reason}"
        )
