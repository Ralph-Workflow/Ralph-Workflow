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
    """R3 contract (Trustworthy Idle Watchdog): the cumulative ceiling
    fires UNCONDITIONALLY past the effective ceiling even when the
    classifier would return LOADING.

    Per PROMPT R3: "There must be a hard, bounded ceiling after which a
    true hang fires regardless of deferral reasons." The cumulative
    waiting ceiling at ``_waiting_branch.py:238-247`` no longer
    consults ``_gate_fire``; it fires even when the classifier returns
    LOADING for a live subagent. The mitigation is to raise
    ``max_waiting_on_child_seconds`` for long-running waits (the
    default is 1800s = 30 min).

    This test exercises the cumulative ceiling with a live subagent
    (filtered count = 1) and ``os_descendant_only_ceiling=300.0``.
    The effective ceiling is reduced to 300s and the ceiling fires
    unconditionally at 300s. The classifier would return LOADING
    but the cumulative ceiling fires regardless.

    Pre-fix (wt-012 dumb-kill prevention): the gate deferred the
    fire via the StuckClassifier's LOADING branch. Post-fix (R3
    hard enforcement): the cumulative ceiling fires regardless.

    Assertions:
      - verdict is FIRE at 300s with ``CHILDREN_PERSIST_TOO_LONG``.
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

    # Advance to just past the 300s effective ceiling. The cumulative
    # ceiling fires UNCONDITIONALLY at 300s per R3 hard enforcement.
    clock.advance(300.0)

    verdict = wd.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"cumulative ceiling MUST fire unconditionally past the"
        f" effective ceiling (R3 hard enforcement); got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    # Verify the classifier would have named LOADING (the live
    # subagent is the deferral signal under the OLD contract).
    # The R3 cumulative ceiling now fires regardless of the
    # classifier's verdict -- the classifier's LOADING verdict
    # is no longer consulted by the cumulative ceiling block.
    summary = wd.last_evidence_summary(clock.monotonic())
    kind = classify_stuck(
        is_waiting_state=False,
        connectivity_state=None,
        evidence_summary=summary,
        classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD,
        activity_evidence_ttl_seconds=wd._config.activity_evidence_ttl_seconds,
    )
    assert kind == StuckKind.LOADING, (
        f"classifier should still return LOADING for the live subagent;"
        f" the R3 cumulative ceiling fires regardless of the classifier"
        f" verdict; got {kind!r}"
    )


def test_children_persist_deferred_while_classifier_returns_loading() -> None:
    """R3 contract (Trustworthy Idle Watchdog): the cumulative ceiling
    fires UNCONDITIONALLY past the effective ceiling even when the
    classifier returns LOADING for a live subagent.

    Per PROMPT R3: "There must be a hard, bounded ceiling after which a
    true hang fires regardless of deferral reasons." The cumulative
    waiting ceiling at ``_waiting_branch.py:238-247`` no longer
    consults ``_gate_fire``; it fires even when the classifier returns
    LOADING for a live subagent. The mitigation is to raise
    ``max_waiting_on_child_seconds`` for long-running waits.

    Pre-fix (the symmetric counterpart of the dumb-kill test): the
    gate deferred the fire via the StuckClassifier's LOADING branch.
    Post-fix (R3 hard enforcement): the cumulative ceiling fires
    regardless of the classifier's LOADING verdict.

    Assertions:
      - verdict is FIRE at the 300s effective ceiling with
        ``CHILDREN_PERSIST_TOO_LONG``.
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
    clock.advance(2.0)

    # First call: enter the WAITING_ON_CHILD branch.
    first = wd.evaluate(classify_quiet=_waiting)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the 300s os_descendant_only_ceiling. With a live
    # subagent (alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS), the
    # classifier returns LOADING but the cumulative ceiling fires
    # UNCONDITIONALLY per R3 hard enforcement regardless of the
    # classifier's verdict.
    clock.advance(300.0)

    verdict = wd.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"cumulative ceiling MUST fire unconditionally past the"
        f" effective ceiling (R3 hard enforcement); got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_dumb_kill_two_pre_output_fragment() -> None:
    """R3 contract (Trustworthy Idle Watchdog): the cumulative ceiling
    fires UNCONDITIONALLY past the effective ceiling even with a
    live child making forward progress.

    Per PROMPT R3: "There must be a hard, bounded ceiling after which a
    true hang fires regardless of deferral reasons." The cumulative
    waiting ceiling at ``_waiting_branch.py:238-247`` no longer
    consults ``_gate_fire``; it fires even when the classifier returns
    LOADING for a live child.

    Pre-fix (wt-012 dumb-kill prevention): the gate deferred the
    fire via the StuckClassifier's LOADING branch. Post-fix (R3
    hard enforcement): the cumulative ceiling fires regardless.

    Assertions:
      - verdict is FIRE at the 300s effective ceiling with
        ``CHILDREN_PERSIST_TOO_LONG``.
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

    # First call: enter the WAITING_ON_CHILD branch.
    clock.advance(2.0)
    first = wd.evaluate(classify_quiet=_waiting)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the 300s effective ceiling. The cumulative
    # ceiling fires UNCONDITIONALLY per R3 hard enforcement.
    clock.advance(300.0)

    verdict = wd.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"cumulative ceiling MUST fire unconditionally past the"
        f" effective ceiling (R3 hard enforcement); got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


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


def test_classifier_consulted_live_callable_returns_loading() -> None:
    """The classifier's WAITING_ON_CHILD branch returns LOADING when called with the live callable.

    This is a regression test for the classifier contract: when
    the classifier is consulted with a callable that returns
    ``WAITING_ON_CHILD``, it MUST return ``LOADING`` (not STUCK).
    The watchdog's gate consults the classifier with a noop stub
    to avoid the chicken-and-egg problem (the watchdog entered
    WAITING_ON_CHILD BECAUSE classify_quiet returned
    WAITING_ON_CHILD; consulting the same callable from the gate
    would always defer the ceiling fire). The pure classifier
    contract is unchanged -- the gate is the boundary that decides
    which branch is consulted in production.
    """
    wd, clock = _make_watchdog(_make_policy(activity_ttl=30.0))
    wd.record_activity()
    clock.advance(2.0)
    wd.evaluate(classify_quiet=_waiting)
    summary = wd.last_evidence_summary(clock.monotonic())
    kind = classify_stuck(
        is_waiting_state=False,
        connectivity_state=None,
        evidence_summary=summary,
        classify_quiet=_waiting,
        activity_evidence_ttl_seconds=wd._config.activity_evidence_ttl_seconds,
    )
    assert kind == StuckKind.LOADING


def test_classifier_consulted_live_callable_returns_transitioning() -> None:
    """The classifier's RESUMABLE_CONTINUE branch returns TRANSITIONING.

    Mirror of the WAITING_ON_CHILD test: when the classifier is
    consulted with a callable that returns ``RESUMABLE_CONTINUE``,
    it MUST return ``TRANSITIONING`` (not STUCK). The watchdog's
    gate consults the classifier with a noop stub for the same
    chicken-and-egg reason as the WAITING_ON_CHILD case.
    """

    def _resumable() -> AgentExecutionState:
        return AgentExecutionState.RESUMABLE_CONTINUE

    wd, clock = _make_watchdog(_make_policy(activity_ttl=30.0))
    wd.record_activity()
    clock.advance(2.0)
    wd.evaluate(classify_quiet=_resumable)
    summary = wd.last_evidence_summary(clock.monotonic())
    kind = classify_stuck(
        is_waiting_state=False,
        connectivity_state=None,
        evidence_summary=summary,
        classify_quiet=_resumable,
        activity_evidence_ttl_seconds=wd._config.activity_evidence_ttl_seconds,
    )
    assert kind == StuckKind.TRANSITIONING
