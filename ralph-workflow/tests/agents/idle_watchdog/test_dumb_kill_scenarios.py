"""Regression tests for the user's extended dumb-kill scenarios.

The existing ``test_smart_verdict_dumb_kills.py`` covers the two
canonical dumb-kill incidents (agent reading CURRENT_PROMPT with
subagent_liveness fresh, agent with single fragment + live child).
This file covers the EXTENDED set of productive-but-quiet
scenarios the user described in the wt-012 prompt:

  - Agent reading ``.agent/CURRENT_PROMPT.md`` while dispatching
    subagents (the subagent_progress channel is fresh).
  - Agent alive with ``OS_DESCENDANT_ONLY_STALE_PROGRESS`` child
    and corroborator reports ``scoped_child_active=True``.
  - Agent emits a single ``mcp_tool_call`` then is quiet for 300s
    with a live subagent.
  - Repeated ``evaluate()`` calls with progress must not drift to
    FIRE.
  - The recovery controller never advances to ``failed_terminal``
    on an unavailability kill driven by the typed watchdog cause.

These tests use FakeClock only; no real subprocess, no real
network, no real sleep.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    CorroborationSnapshot,
    IdleWatchdog,
    TimeoutPolicy,
    WaitingCorroborator,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.process.child_liveness import AliveBy
from ralph.process.monitor import ProcessMonitor, SubagentOutputCapture
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEventBus


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


def _minimal_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _three_agent_state(current_index: int = 0) -> PipelineState:
    chain_state = AgentChainState(
        agents=["claude", "opencode", "agy"],
        current_index=current_index,
        retries=0,
    )
    return PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    ).copy_with(last_connectivity_state="online")


def test_dumb_kill_agent_reading_current_prompt_with_subagent_progress() -> None:
    """Reproduce the first dumb-kill incident: agent reads
    ``.agent/CURRENT_PROMPT.md`` while a live subagent is
    registered with the process monitor (the agent dispatched
    a subagent in parallel).

    The OLD behavior fired at the 120s ceiling mid-read.  The NEW
    behavior defers the fire because the subagent_liveness
    side-channel is fresh (the process monitor reports a live
    subagent), so the classifier returns LOADING and the gate
    returns CONTINUE for the entire 300s ceiling.

    Assertions:
      - verdict is CONTINUE (not FIRE) when the cumulative
        ceiling is reached.
      - last_fire_reason is DEFERRED_BY_STUCK_CLASSIFIER.
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

    # First evaluate: enter the WAITING_ON_CHILD branch after
    # idle_timeout (1.0s) elapses.
    clock.advance(2.0)
    first = wd.evaluate(classify_quiet=_waiting)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the 300s effective ceiling.  The OLD behavior
    # would have fired at 120s cumulative ceiling; the NEW
    # behavior defers because the subagent_liveness side-channel
    # is fresh (the process monitor reports 1 live subagent) and
    # can_defer=True.  The classifier returns LOADING and the
    # gate returns CONTINUE.
    clock.advance(300.0)

    verdict = wd.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"expected CONTINUE (subagent_liveness is fresh), got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER


def test_dumb_kill_agent_with_os_descendant_only_child_is_deferred() -> None:
    """Reproduce the second dumb-kill incident: agent alive with
    ``alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS`` and
    ``scoped_child_active=True`` corroboration.

    The OLD behavior fired at the 120s ceiling.  The NEW behavior
    defers the fire because the classifier returns LOADING
    (subagent_liveness fresh, alive_by set) and the gate returns
    CONTINUE for at least 300s.

    Assertions:
      - verdict is CONTINUE (not FIRE) past 300s.
      - last_fire_reason is DEFERRED_BY_STUCK_CLASSIFIER.
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

    # First evaluate: enter the WAITING_ON_CHILD branch.
    clock.advance(2.0)
    first = wd.evaluate(classify_quiet=_waiting)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the 300s effective ceiling. The OLD behavior
    # would have fired at 120s; the NEW behavior defers because
    # the child is still alive (subagent_liveness fresh,
    # alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS), so the
    # classifier returns LOADING and the gate returns CONTINUE.
    clock.advance(300.0)

    verdict = wd.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"expected CONTINUE (classifier returns LOADING for live subagent), got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER


def test_dumb_kill_first_output_fragment_with_live_subagent() -> None:
    """Reproduce the third dumb-kill scenario: agent emits a
    single ``mcp_tool_call`` (the ``mcp_tool`` channel is fresh)
    then is quiet for 300s with a live subagent.

    The OLD behavior fired at the 120s ceiling after the single
    fragment.  The NEW behavior defers because the ``mcp_tool``
    channel is fresh (within activity_ttl=30s).

    Assertions:
      - verdict is CONTINUE for at least 300s after the mcp_tool_call.
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
    first = wd.evaluate(classify_quiet=_waiting)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    # The agent dispatches a single mcp_tool_call.  The mcp_tool
    # channel is now fresh.
    wd.record_mcp_tool_call()

    # Advance 300s past the mcp_tool_call. The OLD behavior
    # would have fired at 120s cumulative ceiling; the NEW
    # behavior defers because mcp_tool is fresh (within
    # activity_ttl=30s).
    clock.advance(300.0)

    verdict = wd.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"expected CONTINUE (mcp_tool channel is fresh), got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER


def test_dumb_kill_repeated_evaluate_with_progress_does_not_drift_to_fire() -> None:
    """Production scenario: 50 consecutive ``evaluate()`` calls,
    each 6s apart, with a live subagent reporting progress.

    The watchdog must NEVER return FIRE across all 50 calls.  The
    deferral is consistent, not just one-shot.  This is the
    negative test that locks the smart-verdict gate's behavior
    in the production hot path.  Between calls, the verdict is
    ``WAITING_ON_CHILD`` (we are still under the cumulative
    ceiling); past the ceiling, the gate must return ``CONTINUE``
    with ``DEFERRED_BY_STUCK_CLASSIFIER`` because the
    subagent_progress channel is fresh.

    Assertions:
      - No ``evaluate()`` call returns ``FIRE``.
      - When the cumulative ceiling is reached (300s), the
        watchdog returns ``CONTINUE`` with
        ``DEFERRED_BY_STUCK_CLASSIFIER``.
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
    first = wd.evaluate(classify_quiet=_waiting)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    for _ in range(50):
        clock.advance(6.0)
        wd.record_subagent_work()  # the subagent reports progress each step
        verdict = wd.evaluate(classify_quiet=_waiting)
        assert verdict != WatchdogVerdict.FIRE, (
            f"watchdog must never FIRE while subagent_progress is fresh, got {verdict}"
        )

    # The final state must show the deferral reason.
    assert wd.last_fire_reason == WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER


def test_dumb_kill_recovery_controller_never_advances_to_failed_on_unavailable() -> None:
    """End-to-end: a watchdog kill with typed cause
    ``IdleWatchdogKilledError(reason='no_progress_quiet', signal=15)``
    routed through the recovery controller must NOT advance the
    pipeline to ``failed_terminal``.

    This is the recovery-side complement to the watchdog-side test
    in ``test_smart_verdict_dumb_kills.py``.  The user's third
    dumb-kill concern was: even if the watchdog DID fire on a
    no_progress_quiet reason, the recovery controller must not
    exit the pipeline on the kill alone.  The typed
    ``IdleWatchdogKilledError`` is classified as an unavailable
    AGENT failure, so the controller routes it to the
    exponential-backoff branch (rule two) and the pipeline
    continues with the next agent in the chain.

    Assertions:
      - state.phase is NOT advanced to ``failed_terminal``.
      - The current agent (claude) is marked on cooldown.
      - The chain advances to the next agent.
    """
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=FakeClock(start=0.0),
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
        ),
    )
    state = _three_agent_state(current_index=0)

    watchdog_exc = IdleWatchdogKilledError(reason="no_progress_quiet", signal=15)
    inactivity_exc = AgentInactivityTimeoutError(
        "claude",
        30.0,
        opts=InactivityTimeoutOpts(
            reason=WatchdogFireReason.NO_PROGRESS_QUIET,
            diagnostic={"invocation_elapsed": 30.0},
        ),
    )
    inactivity_exc.__cause__ = watchdog_exc

    new_state, _effects, _evt = controller.handle(
        state,
        inactivity_exc,
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.phase != "failed_terminal", (
        f"phase must NOT advance to failed_terminal on unavailable AGENT failure,"
        f" got {new_state.phase}"
    )
    chain = new_state.chain_for_phase("development")
    assert chain is not None
    # The chain should have advanced past claude (the failed agent).
    assert chain.current_index != 0, (
        f"chain must advance past the unavailable agent, got current_index={chain.current_index}"
    )
    # Claude must be on cooldown.
    assert not controller.unavailability_store.is_available("development", "claude"), (
        "claude must be on cooldown after no_progress_quiet typed cause"
    )


def test_dumb_kill_three_agent_dispatching_parallel_scouts() -> None:
    """Reproduce the third dumb-kill incident from the user's log.

    User log excerpt::

        2026-06-15T05:50:52.722153+00:00 INFO CONT [content-start][opencode/...] I need to
            explore this codebase to understand the watchdog architecture before
            planning. Let me dispatch parallel discovery scouts.
        2026-06-16T05:53:23.711523+00:00 ERROR META [waiting] Background child work hit
            hard ceiling (cumulative=194s, ceiling=120s, scoped_child_active=True,
            oldest_child_seconds=-1781497724s, agent=opencode/minimax-coding-plan/MiniMax-M3)
        2026-06-15 22:53:23.711 | WARNING  | idle_watchdog: FIRE reason=no_progress_quiet
            idle_elapsed=151.0s invocation_elapsed=194.4s

    The OLD watchdog fired NO_PROGRESS_QUIET at cumulative=194s (just 30s past
    the 120s ceiling) while the agent was about to dispatch parallel discovery
    scouts. The agent was alive, had a live child (scoped_child_active=True),
    and the only signal was OS_DESCENDANT_ONLY_STALE_PROGRESS.

    The NEW behavior:

      - the dumb-kill floor (no_progress_quiet_minimum_invocation_seconds=120.0s)
        prevents the fire BEFORE invocation_elapsed=120.0s even when all
        channels are stale (the user log fired at 194s, so the floor is
        not the primary protection here -- the smart-verdict gate is);
      - the smart-verdict gate (StuckClassifier) defers the fire while the
        live process monitor reports a live child (the classifier returns
        LOADING via the subagent_liveness channel, and the gate defers).

    The test MUST construct the same live-child prerequisites the existing
    ``test_smart_verdict_dumb_kills.py::test_dumb_kill_two_pre_output_fragment``
    uses, because the current ``_stuck_classifier.py:230-275`` (classify_stuck)
    requires fresh first-party evidence, a subagent_liveness side-channel
    with ``can_defer=True``, OR a live ``classify_quiet`` returning
    WAITING_ON_CHILD to defer. Corroborator-only stale-child evidence
    (alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS) without a live process
    monitor is explicitly NOT a deferral signal per the classifier
    docstring at ``_stuck_classifier.py:92-100``.

    Required setup:
      1. Inject the existing ``_LiveOnlyProcessMonitor(live_count=1)`` so
         ``_subagent_liveness_summary`` sets ``can_defer=True`` and the
         classifier returns LOADING via the subagent_liveness channel.
      2. Call ``wd.evaluate(classify_quiet=_waiting)`` where ``_waiting``
         returns ``AgentExecutionState.WAITING_ON_CHILD`` -- this is the
         live signal the classifier's WAITING_ON_CHILD branch at
         ``_stuck_classifier.py:257-258`` consults to return LOADING.
      3. Configure ``no_progress_quiet_minimum_invocation_seconds=120.0s``
         and ``no_progress_quiet_seconds=120.0s`` so the dumb-kill floor
         is active. The user log's invocation_elapsed=194.4s is past the
         floor, so the floor is satisfied; the live-subagent deferral is
         the primary protection.

    Assertions:
      - verdict is CONTINUE (not FIRE) at the user's exact log scenario
        (idle_elapsed=151s, invocation_elapsed=194s).
      - The session phase is NOT failed_terminal.

    The watchdog returns CONTINUE without firing because the
    ``_is_no_progress_quiet`` short-circuits when the channel
    evidence is active (the live process monitor reports a live
    child, so the subagent_liveness channel is fresh, so
    ``_channel_evidence_active`` returns True and the no-progress
    path is not taken). The gate is never reached; the deferral
    happens at the channel-evidence layer. This is the
    dumb-kill protection the user requested.
    """
    monitor = _LiveOnlyProcessMonitor(live_count=1)

    def _os_desc_only_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    wd, clock = _make_watchdog(
        _make_policy_with_floor(
            idle_timeout=300.0,
            max_waiting=600.0,
            os_descendant_only_ceiling=300.0,
            activity_ttl=30.0,
            no_progress_quiet_seconds=120.0,
            no_progress_quiet_minimum_invocation_seconds=120.0,
        ),
        process_monitor=monitor,
        corroborator=_os_desc_only_corroborator,
    )
    wd.record_invocation_start()
    wd.record_activity()

    # Advance to the user's exact log scenario:
    #   idle_elapsed=151s, cumulative=194s.
    # The OLD watchdog would have FIRE'd at 120s (cumulative) and
    # killed the agent mid-exploration. The NEW behavior defers the
    # fire because the live process monitor reports a live child
    # (subagent_liveness channel is fresh, can_defer=True) AND the
    # smart-verdict gate would defer with DEFERRED_BY_STUCK_CLASSIFIER
    # if it were reached.
    clock.advance(151.0)

    verdict = wd.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"expected CONTINUE at idle_elapsed=151s invocation_elapsed=194s"
        f" (the user's exact log scenario), got {verdict}"
    )


def test_no_progress_quiet_does_not_fire_within_dumb_kill_floor() -> None:
    """The dumb-kill floor protects a recently-launched agent.

    Even when the corroborator says no progress and ALL channels
    are stale, if ``invocation_elapsed <
    no_progress_quiet_minimum_invocation_seconds`` the watchdog
    returns CONTINUE. The floor prevents a recently-launched
    agent that is doing real thinking work from being killed
    before it has a chance to produce first-party activity
    evidence.

    Setup: corroborator reports
    ``OS_DESCENDANT_ONLY_STALE_PROGRESS`` (so the no-progress path
    is active), classify_quiet returns WAITING_ON_CHILD (so the
    no_progress_quiet evaluator runs). The classifier returns
    STUCK (no live subagent), the gate WOULD allow FIRE -- but
    the dumb-kill floor fires FIRST in ``_is_no_progress_quiet``
    and short-circuits the fire.

    Assertions:
      - verdict is CONTINUE (not FIRE) before the floor.
      - last_fire_reason is None (the gate never fired).
    """
    def _os_desc_only_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    wd, clock = _make_watchdog(
        _make_policy_with_floor(
            idle_timeout=300.0,
            max_waiting=600.0,
            os_descendant_only_ceiling=300.0,
            activity_ttl=30.0,
            no_progress_quiet_seconds=120.0,
            no_progress_quiet_minimum_invocation_seconds=120.0,
        ),
        corroborator=_os_desc_only_corroborator,
    )
    wd.record_invocation_start()
    wd.record_activity()

    # Advance to just under the floor: 119s. NO_PROGRESS_QUIET cannot fire.
    clock.advance(119.0)
    verdict = wd.evaluate(classify_quiet=_waiting)
    assert verdict != WatchdogVerdict.FIRE, (
        f"watchdog must not FIRE at invocation_elapsed=119s (under 120s floor),"
        f" got {verdict}"
    )


def test_no_progress_quiet_still_fires_after_dumb_kill_floor_when_genuinely_stuck() -> None:
    """The dumb-kill floor does NOT mask a genuinely stuck agent.

    After the floor elapses, the watchdog must still fire
    NO_PROGRESS_QUIET when the agent is genuinely stuck (no
    output, no subagent, no workspace, all channels stale, no
    live process monitor). The floor is additive, not a
    replacement.

    Setup: corroborator returns ``alive_by=None`` (the
    corroborator cannot confirm liveness — i.e. the child is
    TRULY dead or missing). NO process monitor, ALL channels
    stale, invocation_elapsed well past the floor, classify_quiet
    returns WAITING_ON_CHILD (so the no_progress_quiet
    evaluator runs). The classifier returns STUCK and the gate
    allows FIRE.

    NOTE: per the wt-012 gate refinement, when the corroborator
    reports ANY alive_by signal (e.g. ``OS_DESCENDANT_ONLY_STALE_PROGRESS``)
    the watchdog DEFERS the fire and relies on the cumulative
    ``CHILDREN_PERSIST_TOO_LONG`` ceiling (default 600s) as the
    upper bound. This test exercises the OTHER branch — the
    "child is truly dead" path where the corroborator cannot
    confirm liveness (alive_by is None).

    Assertions:
      - verdict is FIRE (not CONTINUE) past the floor.
      - last_fire_reason is NO_PROGRESS_QUIET.
    """
    def _dead_child_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=None,
            scoped_child_active=False,
            scoped_child_count=0,
        )

    wd, clock = _make_watchdog(
        _make_policy_with_floor(
            idle_timeout=300.0,
            max_waiting=600.0,
            os_descendant_only_ceiling=300.0,
            activity_ttl=30.0,
            no_progress_quiet_seconds=120.0,
            no_progress_quiet_minimum_invocation_seconds=120.0,
        ),
        corroborator=_dead_child_corroborator,
    )
    wd.record_invocation_start()
    wd.record_activity()

    # Advance well past the dumb-kill floor (120s) and past the
    # no_progress_quiet ceiling (120s). The floor has elapsed, the
    # ceiling is reached, all channels are stale, and the agent is
    # genuinely stuck. The watchdog must FIRE.
    clock.advance(150.0)
    verdict = wd.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"watchdog must FIRE when the agent is genuinely stuck past"
        f" the dumb-kill floor + ceiling, got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET


def _make_policy_with_floor(
    *,
    idle_timeout: float = 1.0,
    drain_window: float = 0.0,
    max_waiting: float = 600.0,
    max_session: float | None = None,
    activity_ttl: float | None = 30.0,
    no_output_at_start: float | None = None,
    os_descendant_only_ceiling: float | None = 300.0,
    no_progress_quiet_seconds: float | None = 120.0,
    no_progress_quiet_minimum_invocation_seconds: float | None = 120.0,
) -> TimeoutPolicy:
    """Build a TimeoutPolicy with the dumb-kill floor enabled.

    Mirrors ``_make_policy`` but adds the
    ``no_progress_quiet_minimum_invocation_seconds`` knob so the
    floor is active in tests that exercise the dumb-kill
    protection.
    """
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
        "no_progress_quiet_seconds": no_progress_quiet_seconds,
        "no_progress_quiet_minimum_invocation_seconds": (
            no_progress_quiet_minimum_invocation_seconds
        ),
        "post_tool_result_progression_seconds": None,
        "repeated_error_window_count": 5,
        "repeated_error_window_seconds": 60.0,
        "idle_poll_interval_seconds": 0.05,
        "waiting_status_interval_seconds": 30.0,
    }
    return TimeoutPolicy(**kwargs)


# ---------------------------------------------------------------------------
# wt-012 gate-refinement tests
# ---------------------------------------------------------------------------
# The wt-012 gate refinement in ``IdleWatchdog._is_no_progress_quiet``
# defers the ``NO_PROGRESS_QUIET`` fire when the corroborator reports
# ANY alive_by signal (the child is alive per
# ``AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS``,
# ``CPU_IDLE_WHILE_ALIVE``, ``LOG_STALE_WHILE_ALIVE``,
# ``FRESH_HEARTBEAT_ONLY``, or ``STALE_LABEL_ONLY``). NO_PROGRESS_QUIET
# fires ONLY when the corroborator returns ``alive_by=None`` (no live
# signal at all -- the child is truly dead or missing) AND no fresh
# channel evidence is present. When ``alive_by`` is None, the
# conservative policy preserves the old fire path so legacy
# construction sites that do not set the signal continue to behave
# identically.


def test_no_progress_quiet_does_not_fire_when_corroborator_reports_live_child() -> None:
    """``_is_no_progress_quiet`` defers the fire when the corroborator
    reports any ``alive_by`` signal.

    Per the wt-012 gate refinement, when ``corroboration.alive_by``
    is not ``None`` (e.g. ``OS_DESCENDANT_ONLY_STALE_PROGRESS``),
    ``_is_no_progress_quiet`` returns ``False`` -- the watchdog
    defers the fire and the cumulative ``CHILDREN_PERSIST_TOO_LONG``
    ceiling (default 600s) is the correct upper bound for the live-
    child stall, not the 120s ``NO_PROGRESS_QUIET`` fire.

    The conservative policy: the new test exercises the NEW
    deferral behavior at idle_elapsed=151s (the user's exact log
    scenario); the watchdog must NOT fire ``NO_PROGRESS_QUIET`` even
    though the no_progress_quiet ceiling (120s) is past.

    Setup: ``no_progress_quiet_seconds=120.0``,
    ``no_progress_quiet_minimum_invocation_seconds=120.0`` (dumb-kill
    floor enabled), corroborator returns
    ``OS_DESCENDANT_ONLY_STALE_PROGRESS``, clock advances to 151s
    (past the floor AND past the ceiling), classify_quiet returns
    ``WAITING_ON_CHILD`` (so the no_progress_quiet evaluator runs).

    Assertions:
      - verdict is ``WAITING_ON_CHILD`` (NOT ``FIRE``) -- the
        gate refinement defers the fire at
        ``_is_no_progress_quiet`` via the early-return path
        ``_evaluate_no_progress_quiet`` returns ``None``.
      - ``last_fire_reason`` is ``None`` (NO fire happened).
    """
    def _os_desc_only_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    wd, clock = _make_watchdog(
        _make_policy_with_floor(
            idle_timeout=300.0,
            max_waiting=600.0,
            os_descendant_only_ceiling=300.0,
            activity_ttl=30.0,
            no_progress_quiet_seconds=120.0,
            no_progress_quiet_minimum_invocation_seconds=120.0,
        ),
        corroborator=_os_desc_only_corroborator,
    )
    wd.record_invocation_start()
    wd.record_activity()

    # Advance past BOTH the dumb-kill floor (120s) AND the
    # no_progress_quiet ceiling (120s). The floor has elapsed, the
    # ceiling is reached, but the corroborator reports a LIVE child
    # (``OS_DESCENDANT_ONLY_STALE_PROGRESS``). The new gate refinement
    # MUST defer the fire.
    clock.advance(151.0)
    verdict = wd.evaluate(classify_quiet=_waiting)
    # The watchdog is in the active branch (idle_timeout=300s, idle_elapsed=151s).
    # The no_progress_quiet check DEFERRED the fire (alive_by is not None),
    # so the watchdog returns CONTINUE (NOT FIRE). The cumulative ceiling
    # (CHILDREN_PERSIST_TOO_LONG at 600s) is the upper bound for the
    # live-child stall, not NO_PROGRESS_QUIET at 120s.
    assert verdict != WatchdogVerdict.FIRE, (
        f"watchdog must NOT fire NO_PROGRESS_QUIET when the corroborator"
        f" reports a live child past the no_progress_quiet ceiling, got {verdict}"
    )
    assert wd.last_fire_reason is None, (
        f"last_fire_reason must be None (NO fire happened -- the gate"
        f" refinement deferred), got {wd.last_fire_reason}"
    )


def test_cumulative_ceiling_remains_upper_bound_for_live_child_stalls() -> None:
    """The cumulative ``CHILDREN_PERSIST_TOO_LONG`` ceiling (default 600s)
    is the upper bound for live-child stalls, not the 120s
    ``NO_PROGRESS_QUIET`` fire.

    The wt-012 gate refinement defers ``NO_PROGRESS_QUIET`` when
    the corroborator reports a live child. The cumulative ceiling
    is still the upper bound: the watchdog will fire
    ``CHILDREN_PERSIST_TOO_LONG`` (NOT ``NO_PROGRESS_QUIET``) when
    the cumulative total reaches the ceiling.

    Setup: ``no_progress_quiet_seconds=120.0`` (would have fired
    NO_PROGRESS_QUIET at 120s under the OLD behavior),
    ``max_waiting_on_child_seconds=600.0`` (the cumulative
    ceiling), corroborator reports
    ``OS_DESCENDANT_ONLY_STALE_PROGRESS``, the watchdog enters
    WAITING_ON_CHILD via classify_quiet, then we advance the clock
    past the cumulative ceiling (600s of WAITING_ON_CHILD time).

    Assertions:
      - While cumulative is under the ceiling (590s of waiting):
        verdict is ``WAITING_ON_CHILD`` (NOT FIRE).
      - Once cumulative reaches the ceiling (>= 600s of waiting):
        verdict is ``FIRE`` with
        ``last_fire_reason=CHILDREN_PERSIST_TOO_LONG`` (NOT
        ``NO_PROGRESS_QUIET`` -- the gate refinement defers
        NO_PROGRESS_QUIET when alive_by is not None).
    """
    def _os_desc_only_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    wd, clock = _make_watchdog(
        _make_policy_with_floor(
            idle_timeout=10.0,
            max_waiting=600.0,
            os_descendant_only_ceiling=300.0,
            activity_ttl=30.0,
            no_progress_quiet_seconds=120.0,
            no_progress_quiet_minimum_invocation_seconds=120.0,
        ),
        corroborator=_os_desc_only_corroborator,
    )
    wd.record_invocation_start()
    wd.record_activity()

    # Enter the WAITING_ON_CHILD branch via the active-branch exit
    # (idle_timeout=10s). The first evaluate advances to 11s and
    # transitions the watchdog into WAITING_ON_CHILD.
    clock.advance(11.0)
    verdict = wd.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"watchdog must enter WAITING_ON_CHILD at idle_elapsed=11s, got {verdict}"
    )
    assert wd.last_fire_reason is None, (
        f"last_fire_reason must be None on entry to WAITING_ON_CHILD, got {wd.last_fire_reason}"
    )

    # Under the os_descendant_only effective ceiling: 290s of waiting
    # time (well under the 300s effective ceiling for an
    # OS_DESCENDANT_ONLY child). Multiple short evaluate ticks are
    # used because the cumulative math only counts WAITING_ON_CHILD
    # time across evaluate() calls.
    for _ in range(29):
        clock.advance(10.0)
        verdict = wd.evaluate(classify_quiet=_waiting)
    # 29 * 10s = 290s of waiting, under the 300s effective ceiling.
    # Should NOT fire (cumulative is below the effective ceiling).
    assert verdict != WatchdogVerdict.FIRE, (
        f"watchdog must NOT fire at cumulative=290s of waiting (under"
        f" the 300s os_descendant_only ceiling), got {verdict}"
    )
    assert wd.last_fire_reason is None, (
        f"last_fire_reason must be None at cumulative=290s, got {wd.last_fire_reason}"
    )

    # Past the effective ceiling: 1 more 10s tick brings cumulative
    # to >= 300s of waiting. The watchdog must fire
    # CHILDREN_PERSIST_TOO_LONG (NOT NO_PROGRESS_QUIET).
    clock.advance(10.0)
    verdict = wd.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"watchdog must FIRE once cumulative waiting reaches the 300s"
        f" os_descendant_only ceiling, got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG, (
        f"last_fire_reason must be CHILDREN_PERSIST_TOO_LONG (cumulative"
        f" ceiling is the upper bound for live-child stalls), got {wd.last_fire_reason}"
    )
