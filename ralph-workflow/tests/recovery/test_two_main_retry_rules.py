from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import AgentChainConfig, AgentDrainConfig
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEvent, FailureEventBus, FalloverEvent
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import FailureClassifier
from ralph.recovery.unavailability_reason import UnavailabilityReason

REPO_ROOT = Path(__file__).resolve().parents[2]


def _minimal_policy_bundle(agents: list[str] | None = None) -> object:
    with tempfile.TemporaryDirectory() as d:
        bundle = load_policy(Path(d) / ".agent")
        if agents is None:
            return bundle
        agents_policy = bundle.agents.model_copy(
            update={
                "agent_chains": {"default_chain": AgentChainConfig(agents=list(agents))},
                "agent_drains": {"development": AgentDrainConfig(chain="default_chain")},
            }
        )
        return bundle.model_copy(update={"agents": agents_policy})


def _no_output_opts(
    reason: WatchdogFireReason = WatchdogFireReason.NO_OUTPUT_AT_START,
) -> InactivityTimeoutOpts:
    return InactivityTimeoutOpts(
        reason=reason,
        diagnostic={"invocation_elapsed": 30.0},
    )


def _stalled_opts() -> InactivityTimeoutOpts:
    return InactivityTimeoutOpts(
        reason=WatchdogFireReason.STALLED_AFTER_TOOL_RESULT,
        diagnostic={"invocation_elapsed": 30.0},
    )


def _three_agent_state(current_index: int = 0, retries: int = 0) -> PipelineState:
    chain_state = AgentChainState(
        agents=["claude", "opencode", "agy"],
        current_index=current_index,
        retries=retries,
    )
    return PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    ).copy_with(last_connectivity_state="online")


def _controller(
    *,
    clock: FakeClock | None = None,
    initial_entries: dict[str, UnavailabilityEntry] | None = None,
    agents: list[str] | None = None,
    unproductive_streak_limit: int = 3,
    unproductive_total_limit: int = 9,
) -> RecoveryController:

    return RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock or FakeClock(start=0.0),
            policy_bundle=_minimal_policy_bundle(agents),
            event_bus=FailureEventBus(),
            unavailability_entries=initial_entries or {},
            agent_max_consecutive_unproductive=unproductive_streak_limit,
            agent_max_total_unproductive=unproductive_total_limit,
        ),
    )


def test_recovery_rule_one_same_agent_retry_for_non_unavailable_failure() -> None:
    controller = _controller()
    state = _three_agent_state(current_index=0, retries=0)

    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_stalled_opts())
    new_state, effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.phase == "development"
    assert new_state.is_waiting_state is False
    assert effects == [], f"expected no effects, got {effects}"

    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 0, (
        f"expected same agent (claude), got index {chain.current_index}"
    )
    assert chain.retries == 1, f"expected retries incremented to 1, got {chain.retries}"


def test_recovery_rule_two_exponential_backoff_to_next_agent_for_unavailable_failure() -> None:
    controller = _controller()
    state = _three_agent_state(current_index=0, retries=0)

    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts())
    new_state, effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.phase == "development", (
        f"phase must not advance to failed_terminal, got {new_state.phase}"
    )
    assert new_state.is_waiting_state is False

    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1, (
        f"chain must advance to next agent (opencode), got index {chain.current_index}"
    )
    assert chain.retries == 0, f"retries must reset on new agent, got {chain.retries}"

    store = controller.unavailability_store
    assert not store.is_available("development", "claude"), (
        "claude must be on cooldown after NO_OUTPUT_AT_START"
    )

    assert effects == [], f"expected no effects, got {effects}"


def test_recovery_never_exits_on_unavailability_when_all_agents_on_cooldown() -> None:
    initial_entries: dict[str, UnavailabilityEntry] = {
        "claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
        "opencode": UnavailabilityEntry(
            unavailable_until_ms=10000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=10000,
            max_backoff_ms=10000,
        ),
        "agy": UnavailabilityEntry(
            unavailable_until_ms=7000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=7000,
            max_backoff_ms=7000,
        ),
    }
    controller = _controller(initial_entries=initial_entries)
    state = _three_agent_state(current_index=0, retries=0)

    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts())
    new_state, effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.phase == "development"
    assert new_state.is_waiting_state is True
    assert new_state.last_retry_delay_ms == 5000
    assert new_state.recovery_cycle_count == 0
    assert effects == [], f"expected no effects (wait state, not exit), got {effects}"


def test_recovery_reconsiders_earlier_agent_after_cooldown_expires_with_wrap_true() -> None:
    clock = FakeClock(start=0.0)
    initial_entries: dict[str, UnavailabilityEntry] = {
        "claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
        "opencode": UnavailabilityEntry(
            unavailable_until_ms=60000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=60000,
            max_backoff_ms=60000,
        ),
        "agy": UnavailabilityEntry(
            unavailable_until_ms=30000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=30000,
            max_backoff_ms=30000,
        ),
    }
    controller = _controller(
        clock=clock,
        initial_entries=initial_entries,
    )
    state = _three_agent_state(current_index=0, retries=0)

    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts())
    state_after_first, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )
    assert state_after_first.is_waiting_state is True
    assert state_after_first.last_retry_delay_ms == 5000

    clock.advance(5.1)

    state_at_opencode = state_after_first.with_phase_chain(
        "development",
        AgentChainState(
            agents=["claude", "opencode", "agy"],
            current_index=1,
            retries=0,
        ),
    )

    exc2 = AgentInactivityTimeoutError("opencode", 30.0, opts=_no_output_opts())
    state_after_second, _effects2, _evt2 = controller.handle(
        state_at_opencode,
        exc2,
        FailureContext(phase="development", agent="opencode"),
    )

    chain = state_after_second.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 0, (
        f"priority agent selection must select claude (the recovered agent), got index"
        f" {chain.current_index}"
    )
    assert state_after_second.is_waiting_state is False


def test_recovery_chain_exhaustion_only_when_every_agent_is_spent() -> None:
    controller = _controller(
        agents=["claude", "opencode", "agy"],
        unproductive_streak_limit=1,
        unproductive_total_limit=1,
    )
    state = _three_agent_state(current_index=0, retries=0)

    for agent in ("claude", "opencode", "agy"):
        exc = AgentInactivityTimeoutError(agent, 30.0, opts=_stalled_opts())
        state, _effects, _evt = controller.handle(
            state,
            exc,
            FailureContext(phase="development", agent=agent),
        )

    assert state.phase == "failed_terminal", (
        f"phase must advance to failed_terminal on chain exhaustion, got {state.phase}"
    )
    assert state.is_waiting_state is False
    assert state.recovery_cycle_count == 1, (
        f"recovery_cycle_count must increment to 1, got {state.recovery_cycle_count}"
    )


def test_recovery_failure_classifier_consults_typed_cause_for_watchdog_kill() -> None:
    classifier = FailureClassifier()
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

    classified = classifier.classify(
        inactivity_exc,
        phase="development",
        agent="claude",
        connectivity_state="online",
    )

    assert classified.category == FailureCategory.AGENT, (
        f"typed watchdog kill must classify as AGENT, got {classified.category}"
    )
    assert classified.is_unavailable is True, (
        f"typed watchdog kill must be unavailable, got is_unavailable={classified.is_unavailable}"
    )


def test_recovery_does_not_call_enter_phase_failed_in_unavailability_branch() -> None:
    controller_path = REPO_ROOT / "ralph" / "recovery" / "controller.py"
    source = controller_path.read_text(encoding="utf-8")
    method_start = source.index("    def _handle_retry_progression(")
    method_end = source.index("\n    def ", method_start + 1)
    method_source = source[method_start:method_end]
    unavailable_branch = (
        "if all(not self._is_agent_available(phase, agent) for agent in chain.agents):"
    )
    unavailable_start = method_source.index(unavailable_branch)
    failure_start = method_source.index("self._enter_phase_failed")

    assert unavailable_start < failure_start, (
        "the all-agents-unavailable branch must precede _enter_phase_failed"
    )
    wait_branch = method_source[unavailable_start:failure_start]
    assert "return (" in wait_branch, (
        "all-agents-unavailable branch must return before phase failure"
    )


def test_no_progress_quiet_with_dead_child_routes_to_exponential_backoff() -> None:
    captured_fallover_events: list[FalloverEvent] = []
    bus = FailureEventBus()

    def _capture(evt: FailureEvent | FalloverEvent) -> None:
        if isinstance(evt, FalloverEvent):
            captured_fallover_events.append(evt)

    bus.subscribe(_capture)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=FakeClock(start=0.0),
            policy_bundle=_minimal_policy_bundle(),
            event_bus=bus,
        ),
    )
    state = _three_agent_state(current_index=0, retries=0)

    watchdog_exc = IdleWatchdogKilledError(
        reason="no_progress_quiet",
        signal=15,
        child_alive=False,
    )
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

    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1, (
        f"chain must advance to opencode (index 1) when NO_PROGRESS_QUIET"
        f" fires with child_alive=False (Rule 2: exponential backoff),"
        f" got current_index={chain.current_index}"
    )
    assert chain.retries == 0, (
        f"chain.retries must reset to 0 on chain advance, got {chain.retries}"
    )
    assert not controller.unavailability_store.is_available("development", "claude"), (
        "claude must be on cooldown in the unavailability store after"
        " NO_PROGRESS_QUIET with child_alive=False (Rule 2)"
    )
    assert len(captured_fallover_events) == 1, (
        f"expected exactly one FalloverEvent, got {len(captured_fallover_events)}"
    )
    fallover = captured_fallover_events[0]
    assert fallover.unavailability_reason == UnavailabilityReason.STALE_CHILD_QUIET, (
        f"FalloverEvent.unavailability_reason must be STALE_CHILD_QUIET for"
        f" NO_PROGRESS_QUIET with child_alive=False (Rule 2), got {fallover.unavailability_reason}"
    )
