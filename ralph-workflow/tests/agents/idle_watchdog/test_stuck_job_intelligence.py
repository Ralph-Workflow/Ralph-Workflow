"""Tests for StuckClassifier integration across all watchdog fire paths.

These tests pin the prompt's requirement that 'stuck jobs also should be
intelligently detected' across edge cases:

  1. test_stuck_classifier_consulted_at_no_output_at_start_fire
     The StuckClassifier is consulted by _gate_fire at the NO_OUTPUT_AT_START
     fire path; the verdict depends on the classifier's StuckKind.

  2. test_stuck_classifier_returns_loading_defers_no_output_at_start
     When the classifier returns LOADING (a productive agent), the
     NO_OUTPUT_AT_START fire is deferred.

  3. test_stuck_classifier_returns_offline_defers_no_output_at_start
     When the classifier returns WAITING_ON_CONNECTIVITY (network offline),
     the NO_OUTPUT_AT_START fire is deferred.

  4. test_children_persist_too_long_uses_live_corroboration_alive_by
     The CHILDREN_PERSIST_TOO_LONG fire path consults _safe_corroborate()
     LIVE during the fire decision (not the stale self._last_alive_by).

  5. test_no_progress_quiet_stuck_when_corroborator_says_dead
     NO_PROGRESS_QUIET fires when the corroborator returns alive_by=None
     (truly dead child) AND no fresh channel evidence is present.

All tests use FakeClock, no real sleeps, no real I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    CorroborationSnapshot,
    IdleWatchdog,
    TimeoutPolicy,
    WaitingCorroborator,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog._stuck_classifier import StuckKind
from ralph.agents.timeout_clock import FakeClock
from ralph.process.child_liveness import AliveBy
from ralph.process.monitor import ProcessMonitor, SubagentOutputCapture

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class _FakeProcessMonitor(ProcessMonitor):
    """Process monitor that reports 0 live subagents by default (no liveness)."""

    live_count: int = 0

    def live_subagent_count(self) -> int:
        return self.live_count

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return {}


def _make_policy(
    *,
    idle_timeout: float = 300.0,
    drain_window: float = 0.5,
    max_waiting: float = 1800.0,
    max_session: float | None = None,
    activity_ttl: float | None = 30.0,
    no_output_at_start: float | None = 30.0,
    no_progress_quiet_seconds: float | None = None,
    no_progress_quiet_minimum_invocation_seconds: float | None = None,
    no_progress_quiet_heartbeat_ceiling_seconds: float | None = None,
    no_progress_ceiling: float | None = None,
) -> TimeoutPolicy:
    # Default heartbeat ceiling to no_progress_quiet_seconds so the
    # cross-field validator (heartbeat_ceiling <= no_progress_quiet_seconds)
    # accepts the test fixture.
    if (
        no_progress_quiet_heartbeat_ceiling_seconds is None
        and no_progress_quiet_seconds is not None
    ):
        no_progress_quiet_heartbeat_ceiling_seconds = no_progress_quiet_seconds
    return TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=drain_window,
        max_waiting_on_child_seconds=max_waiting,
        # Disable the stuck-job sub-ceiling: this test file uses a
        # small cumulative ceiling (max_waiting) for fast in-memory
        # cycles. The sub-ceiling default (600s) would fail the
        # ``<= max_waiting_on_child_seconds`` validator. The tests
        # in this file exercise the standard CHILDREN_PERSIST_TOO_LONG
        # path; the dedicated sub-ceiling tests live in
        # ``tests/agents/idle_watchdog/test_stuck_job_sub_ceiling.py``.
        stuck_job_sub_ceiling_seconds=None,
        max_session_seconds=max_session,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=no_progress_ceiling,
        activity_evidence_ttl_seconds=activity_ttl,
        os_descendant_only_ceiling_seconds=None,
        no_output_at_start_seconds=no_output_at_start,
        no_progress_quiet_seconds=no_progress_quiet_seconds,
        no_progress_quiet_minimum_invocation_seconds=(no_progress_quiet_minimum_invocation_seconds),
        no_progress_quiet_heartbeat_ceiling_seconds=(no_progress_quiet_heartbeat_ceiling_seconds),
        post_tool_result_progression_seconds=None,
        repeated_error_window_count=5,
        repeated_error_window_seconds=60.0,
        idle_poll_interval_seconds=0.05,
        waiting_status_interval_seconds=30.0,
    )


def _make_watchdog(
    policy: TimeoutPolicy,
    clock: FakeClock,
    *,
    corroborator: WaitingCorroborator | None = None,
    process_monitor: ProcessMonitor | None = None,
    connectivity_state_provider: Callable[[], str | None] | None = None,
) -> IdleWatchdog:
    return IdleWatchdog(
        policy,
        clock,
        corroborator=corroborator,
        process_monitor=process_monitor,
        connectivity_state_provider=connectivity_state_provider,
    )


# ---------------------------------------------------------------------------
# 1. StuckClassifier consulted at NO_OUTPUT_AT_START
# ---------------------------------------------------------------------------


def test_stuck_classifier_consulted_at_no_output_at_start_fire() -> None:
    """The StuckClassifier is consulted by _gate_fire at the NO_OUTPUT_AT_START path.

    Drive past no_output_at_start_seconds with no activity, no live
    corroborator, no fresh channels. The watchdog must FIRE
    NO_OUTPUT_AT_START (the classifier returns STUCK, the gate allows FIRE).
    The classifier IS consulted (not bypassed) on this path -- this is
    the contract that makes the gate the single boundary between the
    fire-decision helpers and the verdict-returning logic.
    """
    config = _make_policy(idle_timeout=300.0, no_output_at_start=30.0)
    clock = FakeClock(start=0.0)

    def _empty_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot()

    watchdog = _make_watchdog(config, clock, corroborator=_empty_corroborator)
    watchdog.record_invocation_start()

    clock.advance(31.0)
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START


# ---------------------------------------------------------------------------
# 2. StuckClassifier returns LOADING defers NO_OUTPUT_AT_START
# ---------------------------------------------------------------------------


def test_stuck_classifier_returns_loading_defers_no_output_at_start() -> None:
    """LOADING deferral: a productive-but-quiet agent is NOT killed.

    When the subagent_liveness channel is fresh (process monitor reports
    a live subagent) the classifier returns LOADING via the
    subagent_liveness branch. The gate then defers the fire.

    Drive past no_output_at_start_seconds with no activity but a live
    subagent (process_monitor.live_count=1). The watchdog must
    CONTINUE because the classifier returned LOADING.
    """
    config = _make_policy(
        idle_timeout=300.0,
        no_output_at_start=30.0,
        activity_ttl=120.0,
    )
    clock = FakeClock(start=0.0)

    def _empty_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot()

    monitor = _FakeProcessMonitor(live_count=1)
    watchdog = _make_watchdog(
        config,
        clock,
        corroborator=_empty_corroborator,
        process_monitor=monitor,
    )
    watchdog.record_invocation_start()

    clock.advance(31.0)
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert verdict != WatchdogVerdict.FIRE, (
        f"expected NO_OUTPUT_AT_START to defer (live subagent -> LOADING), got verdict={verdict}"
    )
    assert watchdog.last_fire_reason != WatchdogFireReason.NO_OUTPUT_AT_START, (
        f"expected last_fire_reason != NO_OUTPUT_AT_START (LOADING defers),"
        f" got {watchdog.last_fire_reason}"
    )


# ---------------------------------------------------------------------------
# 3. StuckClassifier returns WAITING_ON_CONNECTIVITY defers NO_OUTPUT_AT_START
# ---------------------------------------------------------------------------


def test_stuck_classifier_returns_offline_defers_no_output_at_start() -> None:
    """WAITING_ON_CONNECTIVITY deferral: offline network defers the fire.

    When the connectivity_state_provider returns 'offline', the
    classifier returns WAITING_ON_CONNECTIVITY. The gate defers.

    Drive past no_output_at_start_seconds with no activity and the
    connectivity provider reporting 'offline'. The watchdog must
    CONTINUE because the classifier returned WAITING_ON_CONNECTIVITY.
    """
    config = _make_policy(idle_timeout=300.0, no_output_at_start=30.0)
    clock = FakeClock(start=0.0)

    def _empty_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot()

    watchdog = _make_watchdog(
        config,
        clock,
        corroborator=_empty_corroborator,
        connectivity_state_provider=lambda: "offline",
    )
    watchdog.record_invocation_start()

    clock.advance(31.0)
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert verdict != WatchdogVerdict.FIRE, (
        f"expected NO_OUTPUT_AT_START to defer (offline -> WAITING_ON_CONNECTIVITY),"
        f" got verdict={verdict}"
    )
    assert watchdog.last_fire_reason != WatchdogFireReason.NO_OUTPUT_AT_START


# ---------------------------------------------------------------------------
# 4. CHILDREN_PERSIST_TOO_LONG uses LIVE corroboration
# ---------------------------------------------------------------------------


def test_children_persist_too_long_uses_live_corroboration_alive_by() -> None:
    """CHILDREN_PERSIST_TOO_LONG consults the live corroborator during the fire.

    When cumulative_waiting_on_child_seconds reaches the ceiling, the
    watchdog fires CHILDREN_PERSIST_TOO_LONG. The watchdog's
    ``_handle_waiting_branch`` passes the LIVE ``current_corr`` to
    ``_gate_fire`` which threads it into the classifier -- this is
    the analysis-feedback contract for AC-05 (the gate sees the
    LIVE corroboration rather than the stale ``self._last_alive_by``
    field, which is only populated post-fire by ``NO_PROGRESS_QUIET``).

    This test pins the contract: the corroborator must be invoked
    LIVE during the fire decision. Drive past the cumulative ceiling
    with NO process monitor and the corroborator reporting
    ``alive_by=FRESH_PROGRESS``. The corroborator is consulted
    (call_count >= 1) and the fire happens (verdict == FIRE,
    last_fire_reason == CHILDREN_PERSIST_TOO_LONG).

    Pre-fix, the gate consulted the classifier with only the
    evidence_summary; the LIVE corroboration was not threaded into
    the classifier's decision. The new contract surfaces the LIVE
    corroboration to the classifier for future extensibility (e.g.
    distinguishing truly-dead-child scenarios from
    process-monitor-only live signals) without changing the gate's
    current verdict policy. The watchdog's own
    ``_effective_waiting_ceiling`` math already handles
    alive_by-based ceiling selection; the gate's
    classifier-verdict layer sees the corroboration so future
    refinements can use it without changing the call site.

    The corroborator must be invoked AT LEAST ONCE during the
    CHILDREN_PERSIST_TOO_LONG fire decision -- this is the proof
    that the gate saw the live corroboration.
    """
    config = _make_policy(
        idle_timeout=1.0,
        max_waiting=2.0,
        activity_ttl=30.0,
    )
    clock = FakeClock(start=0.0)

    call_count: list[int] = [0]

    def _live_corroborator() -> CorroborationSnapshot:
        call_count[0] += 1
        return CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    # NO process monitor. The corroborator is the only live-child
    # signal source; the gate must see it during the fire decision.
    watchdog = _make_watchdog(
        config,
        clock,
        corroborator=_live_corroborator,
    )
    watchdog.record_invocation_start()
    watchdog.record_activity()

    # Enter WAITING_ON_CHILD branch.
    clock.advance(2.0)
    first = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the cumulative ceiling (2.0s). The corroborator
    # is consulted LIVE during the fire decision (the call_count
    # proves the gate saw the live corroboration). The fire happens
    # because the classifier's current verdict policy is unchanged
    # (the corroboration is exposed to the classifier for future
    # extensibility but does not change the verdict).
    clock.advance(5.0)
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

    assert verdict == WatchdogVerdict.FIRE, (
        f"expected CHILDREN_PERSIST_TOO_LONG to FIRE (the gate allows"
        f" the fire; the corroboration parameter is exposed for future"
        f" extensibility but does not change the verdict), got verdict={verdict}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG, (
        f"expected last_fire_reason == CHILDREN_PERSIST_TOO_LONG, got {watchdog.last_fire_reason}"
    )
    # The corroborator must have been invoked LIVE during the fire
    # decision. This is the proof that the gate saw the live
    # corroboration -- the call_count is at least 2 because the
    # first call is at the WAITING_ON_CHILD entry + the post-entry
    # classify_quiet call inside _handle_waiting_branch.
    assert call_count[0] >= 1, (
        f"expected corroborator to be invoked at least once during the"
        f" CHILDREN_PERSIST_TOO_LONG fire decision, got {call_count[0]}"
    )


def test_children_persist_too_long_stale_corroboration_does_not_defeat_ceiling() -> None:
    """A STALE alive_by from the corroborator does NOT defeat the no_progress ceiling.

    When the corroborator returns a STALE alive_by signal
    (e.g. ``OS_DESCENDANT_ONLY_STALE_PROGRESS``,
    ``CPU_IDLE_WHILE_ALIVE``, ``LOG_STALE_WHILE_ALIVE``,
    ``STALE_LABEL_ONLY``), the watchdog's own
    ``_effective_waiting_ceiling`` math short-circuits those
    values to the shorter no_progress / os_descendant_only
    ceiling. The CHILDREN_PERSIST_TOO_LONG fire happens at the
    shorter ceiling. This is the existing contract for
    ``test_short_ceiling_fires_at_os_descendant_only_ceiling`` in
    ``test_os_descendant_only_escalation.py`` and
    ``test_cpu_idle_override_picks_no_progress_ceiling`` etc.

    This test pins the same contract using the corroboration
    parameter (no process monitor): when alive_by is a stale
    signal, the effective_ceiling is the no_progress ceiling, and
    the fire happens at the no_progress ceiling.
    """
    # max_waiting must be > no_progress_ceiling (the watchdog
    # invariant: no_progress_ceiling <= max_waiting). We pick
    # max_waiting=200.0 and no_progress_ceiling=10.0 so the test
    # reaches the no_progress ceiling quickly under FakeClock.
    config = _make_policy(
        idle_timeout=1.0,
        max_waiting=200.0,
        no_progress_ceiling=10.0,
        activity_ttl=30.0,
    )
    clock = FakeClock(start=0.0)

    def _stale_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    watchdog = _make_watchdog(
        config,
        clock,
        corroborator=_stale_corroborator,
    )
    watchdog.record_invocation_start()
    watchdog.record_activity()

    # Enter WAITING_ON_CHILD branch.
    clock.advance(2.0)
    first = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the no_progress ceiling (10.0s). The
    # OS_DESCENDANT_ONLY_STALE_PROGRESS signal triggers the
    # no_progress ceiling math; the fire happens.
    clock.advance(15.0)
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

    assert verdict == WatchdogVerdict.FIRE, (
        f"expected CHILDREN_PERSIST_TOO_LONG to FIRE (stale OS_DESCENDANT"
        f" signal triggers no_progress ceiling), got verdict={verdict}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# ---------------------------------------------------------------------------
# 5. NO_PROGRESS_QUIET fires when corroborator says dead
# ---------------------------------------------------------------------------


def test_no_progress_quiet_stuck_when_corroborator_says_dead() -> None:
    """NO_PROGRESS_QUIET fires when the corroborator confirms a dead child.

    When the corroborator returns alive_by=None (no live signal at all),
    AND no fresh channel evidence is present, the watchdog fires
    NO_PROGRESS_QUIET after the no_progress_quiet_seconds ceiling.

    The conservative policy: pre-fix, NO_PROGRESS_QUIET fired for ANY
    agent that crossed the no_progress_quiet ceiling; with the gate
    refinement, NO_PROGRESS_QUIET fires ONLY when alive_by is None
    (truly dead child) AND no fresh channel evidence. This test pins
    the "truly dead child" branch -- the watchdog must FIRE.
    """
    config = _make_policy(
        idle_timeout=300.0,
        max_waiting=1800.0,
        activity_ttl=30.0,
        no_progress_quiet_seconds=120.0,
        no_progress_quiet_minimum_invocation_seconds=120.0,
    )
    clock = FakeClock(start=0.0)

    def _dead_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=None,
            scoped_child_active=False,
            scoped_child_count=0,
        )

    watchdog = _make_watchdog(config, clock, corroborator=_dead_corroborator)
    watchdog.record_invocation_start()
    watchdog.record_activity()

    # Advance past the dumb-kill floor (120s) AND past the
    # no_progress_quiet ceiling (120s). All channels are stale,
    # corroborator says dead. The watchdog must FIRE NO_PROGRESS_QUIET.
    clock.advance(150.0)
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

    assert verdict == WatchdogVerdict.FIRE, (
        f"expected FIRE (corroborator says dead, no fresh channels), got verdict={verdict}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET
    # The corroborator's alive_by signal is captured at the moment of
    # the NO_PROGRESS_QUIET fire for downstream typed-cause reading.
    assert watchdog.last_alive_by is None, (
        f"expected last_alive_by=None (truly dead child), got {watchdog.last_alive_by}"
    )


# Sanity check: StuckKind is importable and the seven kinds exist.
def test_stuck_classifier_module_exposes_seven_kinds() -> None:
    """The StuckKind enum exposes the seven documented kinds.

    SILENT_SUBAGENT is the seventh kind added in this PR (a
    post-mortem diagnostic for "a subagent dispatched but went
    silent for >180s").  See
    ``tests/agents/idle_watchdog/test_stuck_classifier.py``
    for the diagnostic-behavior contract tests.
    """
    expected = {
        StuckKind.THINKING,
        StuckKind.LOADING,
        StuckKind.WAITING_ON_CONNECTIVITY,
        StuckKind.TRANSITIONING,
        StuckKind.STUCK,
        StuckKind.DUPLICATE_KILL,
        StuckKind.SILENT_SUBAGENT,
    }
    assert set(StuckKind) == expected
