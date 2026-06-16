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
