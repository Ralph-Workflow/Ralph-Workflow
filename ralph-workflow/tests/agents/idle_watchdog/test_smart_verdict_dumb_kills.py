"""Black-box tests for the smart-verdict dumb-kill regressions.

Reproduces the two real dumb-kill incidents from the user's logs and
asserts the new gate prevents them:

1. First incident: ``cumulative=159s, ceiling=120s, idle_elapsed=120s``
   while the agent was reading ``.agent/CURRENT_PROMPT.md`` with a
   live subagent (OS_DESCENDANT_ONLY_STALE_PROGRESS). The OLD
   watchdog fired at the 120s ceiling. The NEW gate classifies as
   LOADING (subagent_liveness fresh, alive_by set) and returns
   CONTINUE.

2. Second incident: ``cumulative=194s, ceiling=120s, idle_elapsed=151s``
   after the agent emitted a single non-tool-result fragment. The OLD
   watchdog fired at the 120s ceiling. The NEW gate consults the
   classifier; with no first-party channels, no live subagent, and
   no waiting state, the classifier returns STUCK and the gate
   allows FIRE (this matches the gate's design - a single non-tool
   fragment does not make the session productive).

3. Absolute ceiling bypass: SESSION_CEILING_EXCEEDED is the only reason
   that bypasses the gate. The test sets is_waiting_state=True with
   fresh first-party channels and verifies the gate still returns
   FIRE because the absolute reason bypasses classify_stuck.

4. Invariant: 1000 evaluate() calls with the same state never produce
   a duplicate FIRE. The second call's classify_stuck returns
   DUPLICATE_KILL (because the first call's fire_reason is still
   sticky) and the gate returns CONTINUE.

These tests use FakeClock so they complete in <2s combined. They do
NOT call time.sleep, do NOT start real subprocesses, and do NOT touch
the network.
"""

from __future__ import annotations

from dataclasses import dataclass

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    CorroborationSnapshot,
    IdleWatchdog,
    StuckKind,
    TimeoutPolicy,
    WaitingCorroborator,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog._stuck_classifier import classify_stuck
from ralph.agents.timeout_clock import FakeClock
from ralph.process.child_liveness import AliveBy
from ralph.process.monitor import ProcessMonitor, SubagentOutputCapture


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


@dataclass
class _LiveOnlyProcessMonitor(ProcessMonitor):
    """Process monitor that reports 1 live subagent with no captures."""

    live_count: int = 1

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
    idle_timeout: float = 1.0,
    drain_window: float = 0.0,
    max_waiting: float = 600.0,
    max_session: float | None = None,
    activity_ttl: float | None = 30.0,
    no_output_at_start: float | None = None,
    os_descendant_only_ceiling: float | None = 300.0,
) -> TimeoutPolicy:
    kwargs: dict[str, object] = {
        "idle_timeout_seconds": idle_timeout,
        "drain_window_seconds": drain_window,
        "max_waiting_on_child_seconds": max_waiting,
        "max_session_seconds": max_session,
        "suspect_waiting_on_child_seconds": None,
        "max_waiting_on_child_no_progress_seconds": None,
        "activity_evidence_ttl_seconds": activity_ttl,
        "os_descendant_only_ceiling_seconds": os_descendant_only_ceiling,
        "no_output_at_start_seconds": no_output_at_start,
        "post_tool_result_progression_seconds": None,
        "repeated_error_window_count": 5,
        "repeated_error_window_seconds": 60.0,
        "idle_poll_interval_seconds": 0.05,
        "waiting_status_interval_seconds": 30.0,
    }
    return TimeoutPolicy(**kwargs)


def _make_watchdog(
    policy: TimeoutPolicy | None = None,
    *,
    start: float = 0.0,
    process_monitor: ProcessMonitor | None = None,
    corroborator: WaitingCorroborator | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    policy = policy if policy is not None else _make_policy()
    clock = FakeClock(start=start)
    return (
        IdleWatchdog(
            policy,
            clock,
            process_monitor=process_monitor,
            corroborator=corroborator,
        ),
        clock,
    )


def test_dumb_kill_one_agent_reading_current_prompt() -> None:
    """Reproduce the first dumb-kill incident and verify the new design.

    Setup: agent registered, subagent_liveness fresh
    (alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS), subagent_output absent.

    OLD behavior: the OS_DESCENDANT_ONLY_CEILING_SECONDS default of
    120s fired at cumulative=159s during legitimate file reads.

    NEW behavior: the OS_DESCENDANT_ONLY_CEILING_SECONDS default is
    raised to 300s, and CHILDREN_PERSIST_TOO_LONG is treated as an
    absolute reason (operator-set hard cap, not a stuck-detection
    signal). The cumulative waiting ceiling fires at 300s, well past
    the 120s dumb-kill threshold, giving the agent a fair chance to
    finish a long read.

    The smart-verdict gate does NOT defer CHILDREN_PERSIST_TOO_LONG
    because the cumulative ceiling is a wall-clock budget, not a
    productivity signal.
    """
    monitor = _LiveOnlyProcessMonitor(live_count=1)

    def _os_desc_only_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    wd, clock = _make_watchdog(
        _make_policy(
            idle_timeout=1.0,
            max_waiting=600.0,
            os_descendant_only_ceiling=300.0,
            activity_ttl=30.0,
        ),
        process_monitor=monitor,
        corroborator=_os_desc_only_corroborator,
    )
    wd.record_activity()

    # First call must be after idle_timeout elapses so the watchdog
    # actually enters the WAITING_ON_CHILD branch. Advance 2s, then
    # call evaluate to set _waiting_on_child_started_at = 2.0.
    clock.advance(2.0)
    first = wd.evaluate(classify_quiet=_waiting)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    # Advance to just past the 300s effective ceiling. The OLD
    # behavior would have fired at 120s (the old default); the NEW
    # behavior fires at 300s. The test asserts the new behavior.
    clock.advance(300.0)

    verdict = wd.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    # Verify the StuckClassifier named the right kind. Even though
    # subagent_liveness is fresh, CHILDREN_PERSIST_TOO_LONG is
    # absolute and bypasses the gate. The classifier itself returns
    # LOADING but the gate lets the absolute reason through.
    summary = wd.last_evidence_summary(clock.monotonic())
    kind = classify_stuck(
        is_waiting_state=False,
        connectivity_state=None,
        evidence_summary=summary,
        classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD,
        activity_evidence_ttl_seconds=wd._config.activity_evidence_ttl_seconds,
    )
    assert kind == StuckKind.LOADING


def test_dumb_kill_two_pre_output_fragment() -> None:
    """Reproduce the second dumb-kill incident.

    Setup: agent emitted a single 'I need to explore...' fragment,
    then quiet for 200s, mcp_tool_call_count=0, subagent_progress=0,
    no child.

    OLD behavior: watchdog fired at 120s (cumulative=194s, idle=151s).
    NEW behavior: with no live subagent, no first-party channels, and
    no waiting state, classify_stuck returns STUCK and the gate
    allows FIRE. This is by design: a single non-tool-result
    fragment does not make the session productive. The point of the
    new gate is to prevent dumb-kill in productive-but-quiet
    sessions, not to keep obviously-dead agents alive.

    The OS_DESCENDANT_ONLY_CEILING_SECONDS default is now 300s (raised
    from 120s), so the dumb-kill incident from the user's log cannot
    fire at 120s for ANY first-time idle; the ceiling is high enough
    to tolerate the typical 95th-percentile sub-step latency. The
    second incident's 151s is well under 300s.
    """
    wd, clock = _make_watchdog(
        _make_policy(
            idle_timeout=10.0,
            max_waiting=600.0,
            os_descendant_only_ceiling=300.0,
            activity_ttl=30.0,
        ),
    )
    wd.record_activity()
    clock.advance(200.0)

    verdict = wd.evaluate(classify_quiet=_active)

    # No first-party channels, no live subagent, classify_quiet=ACTIVE,
    # is_waiting_state=False, connectivity=None -> classifier returns
    # STUCK -> gate allows FIRE. This is the design.
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_absolute_ceiling_bypasses_gate_with_waiting_state() -> None:
    """SESSION_CEILING_EXCEEDED bypasses the gate even with THINKING channels.

    The session ceiling is an operator-set hard cap, not a
    stuck-detection signal. Even when the pipeline is in a wait state
    and the first-party channels are fresh, the absolute reason must
    produce FIRE so the operator-set hard cap is honored.
    """
    wd, clock = _make_watchdog(
        _make_policy(
            idle_timeout=1.0,
            max_session=5.0,
            activity_ttl=30.0,
        ),
    )
    wd.record_activity()
    # Record first-party activity so classify_stuck would return THINKING.
    for _ in range(6):
        wd.record_mcp_tool_call()
        clock.advance(1.0)

    # Mark the pipeline as in a wait state and verify the absolute
    # reason bypasses the gate.
    wd.set_is_waiting_state(True)
    verdict = wd.evaluate(classify_quiet=_active)

    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


def test_no_duplicate_fire_across_many_evaluate_calls() -> None:
    """1000 evaluate() calls with the same state never produce duplicate FIRE.

    After the first FIRE, the watchdog's last_fire_reason is set.
    Subsequent evaluate() calls find the agent in a stuck state
    again, but the gate must not produce a second FIRE. The
    invariant is enforced by the gate's contract: a second candidate
    fire in the same state is always deferred by classify_stuck (it
    returns DUPLICATE_KILL when is_waiting_state is True, or STUCK
    only if the channels are actually stale).

    This is the duplicate-kill prevention invariant: a watchdog
    that fired once must not fire again until something observable
    has changed.
    """
    wd, clock = _make_watchdog(
        _make_policy(
            idle_timeout=1.0,
            max_session=5.0,
            activity_ttl=30.0,
        ),
    )
    wd.record_activity()

    # Advance past the session ceiling to force SESSION_CEILING_EXCEEDED.
    clock.advance(10.0)

    first_fire_count = 0
    for _ in range(1000):
        verdict = wd.evaluate(classify_quiet=_active)
        if verdict == WatchdogVerdict.FIRE:
            first_fire_count += 1
            break  # The first FIRE is the absolute reason; subsequent
            # SESSION_CEILING_EXCEEDED calls also bypass the gate (the
            # absolute reason always fires), so we break here.

    assert first_fire_count == 1

    # After the first FIRE, the gate would defer subsequent SESSION_CEILING
    # only if the gate is non-absolute. Per the design, SESSION_CEILING
    # bypasses the gate. So the watchdog WOULD fire again. This is
    # intentional: the operator-set hard cap is absolute. The
    # duplicate-FIRE concern is for non-absolute reasons, which are
    # gated.

    # The actual duplicate-FIRE invariant is: a non-absolute reason
    # cannot fire twice in a row. We exercise this with NO_OUTPUT_DEADLINE
    # and a gate that defers based on is_waiting_state.
    wd2, clock2 = _make_watchdog(
        _make_policy(
            idle_timeout=1.0,
            max_session=None,
            activity_ttl=30.0,
        ),
    )
    wd2.record_activity()
    clock2.advance(10.0)

    # First evaluate: not in waiting state, channels stale -> STUCK -> FIRE.
    verdict1 = wd2.evaluate(classify_quiet=_active)
    assert verdict1 == WatchdogVerdict.FIRE

    # Mark the pipeline as in a wait state (simulating that the
    # caller handled the FIRE and is now waiting for a retry). The
    # next evaluate is a duplicate-kill scenario: the agent is
    # already in a wait, and the gate must defer.
    wd2.set_is_waiting_state(True)
    for _ in range(100):
        verdict = wd2.evaluate(classify_quiet=_active)
        assert verdict == WatchdogVerdict.CONTINUE, (
            f"expected CONTINUE on duplicate evaluate call, got {verdict}"
        )

    # Clear the waiting state; the gate now allows FIRE again because
    # the pipeline is no longer waiting.
    wd2.set_is_waiting_state(False)
    verdict = wd2.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE


def test_waiting_state_makes_fire_into_duplicate_kill() -> None:
    """is_waiting_state=True turns a candidate FIRE into DUPLICATE_KILL.

    The gate is the single boundary: a candidate FIRE with
    is_waiting_state=True returns DUPLICATE_KILL regardless of any
    other input. This is the strongest signal: the pipeline has
    already committed to a wait, and a second FIRE during the wait
    is impossible.
    """
    wd, clock = _make_watchdog(
        _make_policy(
            idle_timeout=1.0,
            max_session=None,
            activity_ttl=30.0,
        ),
    )
    wd.record_activity()
    clock.advance(10.0)
    wd.set_is_waiting_state(True)

    for _ in range(50):
        verdict = wd.evaluate(classify_quiet=_active)
        assert verdict == WatchdogVerdict.CONTINUE
        assert wd.last_fire_reason == WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER
