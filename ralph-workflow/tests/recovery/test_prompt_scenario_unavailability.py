from __future__ import annotations

import importlib
import tempfile
from pathlib import Path

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.invoke import AgentInvocationError
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_unavailability_tracker import (
    AgentUnavailabilityTracker,
    UnavailabilityEntry,
    UnavailabilityStore,
)
from ralph.recovery.classifier import FailureClassifier, FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEventBus
from ralph.recovery.unavailability_reason import (
    DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    ReasonBackoffPolicy,
    UnavailabilityReason,
)
from ralph.timeout_defaults import NO_OUTPUT_AT_START_SECONDS


def _minimal_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _no_output_opts() -> InactivityTimeoutOpts:
    return InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_OUTPUT_AT_START,
        diagnostic={"invocation_elapsed": 30.0},
    )


def _no_progress_opts() -> InactivityTimeoutOpts:
    return InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_PROGRESS_QUIET,
        diagnostic={"invocation_elapsed": 60.0},
    )


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


def _build_default_watchdog(clock: FakeClock) -> IdleWatchdog:
    policy = TimeoutPolicy(idle_timeout_seconds=300.0)
    return IdleWatchdog(policy, clock)


def _wait_state_backoff_policy() -> dict[UnavailabilityReason, ReasonBackoffPolicy]:
    return {
        UnavailabilityReason.NO_OUTPUT_AT_START: ReasonBackoffPolicy(
            base_backoff_ms=15_000,
            max_backoff_ms=30_000,
        ),
    }


def test_prompt_scenario_five_hour_stall_caught_at_the_startup_grace() -> None:
    clock = FakeClock(start=0.0)
    watchdog = _build_default_watchdog(clock)
    watchdog.record_invocation_start()

    clock.advance(NO_OUTPUT_AT_START_SECONDS + 1.0)

    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START

    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
        ),
    )
    state = _three_agent_state()

    exc = AgentInactivityTimeoutError("claude", 120.0, opts=_no_output_opts())
    state, _effects, _failure_evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    chain = state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1

    snapshot = controller.snapshot()
    claude_cooldown_ms = snapshot["unavailable_timeouts"]["claude"]
    current_time_ms = int(clock.monotonic() * 1000)
    claude_remaining_ms = claude_cooldown_ms - current_time_ms
    assert 5_000 <= claude_remaining_ms <= 30_000, (
        f"NO_OUTPUT_AT_START remaining cooldown must be within the "
        f"5_000-30_000ms window from DEFAULT_UNAVAILABILITY_BACKOFF_POLICY, "
        f"got {claude_remaining_ms}ms (snapshot={claude_cooldown_ms}ms, "
        f"now={current_time_ms}ms)"
    )

    available = controller.agents_now_available(
        "development",
        ["claude", "opencode", "agy"],
    )
    assert "opencode" in available
    assert "claude" not in available


def test_prompt_scenario_distinguishes_out_of_credits_from_subagents() -> None:
    classifier = FailureClassifier()

    classified_a = classifier.classify(
        "You've hit your weekly limit",
        phase="development",
        agent="claude",
        connectivity_state="online",
    )
    assert classified_a.unavailability_reason == UnavailabilityReason.OUT_OF_CREDITS

    classified_b = classifier.classify(
        AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts()),
        phase="development",
        agent="claude",
        connectivity_state="online",
    )
    assert classified_b.unavailability_reason == UnavailabilityReason.NO_OUTPUT_AT_START

    classified_c = classifier.classify(
        AgentInactivityTimeoutError("claude", 60.0, opts=_no_progress_opts()),
        phase="development",
        agent="claude",
        connectivity_state="online",
    )
    assert classified_c.unavailability_reason == UnavailabilityReason.STALE_CHILD_QUIET

    classified_typed = classifier.classify(
        AgentInvocationError("claude", 1, "You've hit your weekly limit"),
        phase="development",
        agent="claude",
        connectivity_state="online",
    )
    assert classified_typed.is_unavailable is True
    assert classified_typed.unavailability_reason == UnavailabilityReason.OUT_OF_CREDITS

    clock = FakeClock(start=0.0)
    tracker = AgentUnavailabilityTracker(clock=clock)

    out_of_credits_entry = tracker.mark_unavailable(
        "development",
        "claude",
        UnavailabilityReason.OUT_OF_CREDITS,
    )
    assert out_of_credits_entry.base_backoff_ms == 60_000
    assert out_of_credits_entry.max_backoff_ms == 18_000_000

    no_output_entry = tracker.mark_unavailable(
        "development",
        "opencode",
        UnavailabilityReason.NO_OUTPUT_AT_START,
    )
    assert no_output_entry.base_backoff_ms == 5_000
    assert no_output_entry.max_backoff_ms == 18_000_000

    stale_child_entry = tracker.mark_unavailable(
        "development",
        "agy",
        UnavailabilityReason.STALE_CHILD_QUIET,
    )
    assert stale_child_entry.base_backoff_ms == 15_000
    assert stale_child_entry.max_backoff_ms == 18_000_000

    policy = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY
    assert policy[UnavailabilityReason.OUT_OF_CREDITS].base_backoff_ms == 60_000
    assert policy[UnavailabilityReason.OUT_OF_CREDITS].max_backoff_ms == 18_000_000
    assert policy[UnavailabilityReason.NO_OUTPUT_AT_START].base_backoff_ms == 5_000
    assert policy[UnavailabilityReason.NO_OUTPUT_AT_START].max_backoff_ms == 18_000_000
    assert policy[UnavailabilityReason.STALE_CHILD_QUIET].base_backoff_ms == 15_000
    assert policy[UnavailabilityReason.STALE_CHILD_QUIET].max_backoff_ms == 18_000_000

    for reason in (
        UnavailabilityReason.OUT_OF_CREDITS,
        UnavailabilityReason.NO_OUTPUT_AT_START,
        UnavailabilityReason.STALE_CHILD_QUIET,
    ):
        rp = policy[reason]
        assert rp.base_backoff_ms < rp.max_backoff_ms
        assert rp.base_backoff_ms > 0


def test_prompt_scenario_forever_wait_through_many_cooldown_cycles() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
            unavailability_backoff_policy=_wait_state_backoff_policy(),
        ),
    )
    state = _three_agent_state()
    opts = _no_output_opts()

    for agent_name in ("claude", "opencode", "agy"):
        exc = AgentInactivityTimeoutError(agent_name, 30.0, opts=opts)
        state, _effects, _failure_evt = controller.handle(
            state,
            exc,
            FailureContext(phase="development", agent=agent_name),
        )

    chain = state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == len(chain.agents) - 1
    assert state.is_waiting_state is True, (
        f"expected wait state to be entered after all 3 agents marked "
        f"unavailable, got is_waiting_state={state.is_waiting_state!r}"
    )
    assert state.recovery_cycle_count == 0
    assert state.last_retry_delay_ms > 0
    assert state.last_retry_delay_ms <= 30_000

    for _ in range(10):
        clock.advance(1.0)

        current_agent = chain.agents[chain.current_index]
        exc = AgentInactivityTimeoutError(current_agent, 30.0, opts=opts)
        state, _effects, _failure_evt = controller.handle(
            state,
            exc,
            FailureContext(phase="development", agent=current_agent),
        )

        assert state.phase == "development", (
            f"wait state must never enter 'failed_terminal', got phase={state.phase!r}"
        )
        assert state.recovery_cycle_count == 0, (
            "wait state must never consume the recovery cycle cap"
        )
        assert state.last_retry_delay_ms > 0, (
            "wait state must always have a positive sleep so the run loop can resume"
        )
        assert state.last_retry_delay_ms <= 30_000, (
            f"wait state delay must be bounded by NO_OUTPUT_AT_START "
            f"max (30_000ms), got {state.last_retry_delay_ms}ms"
        )

    clock.advance(6.0)

    current_agent = chain.agents[chain.current_index]
    exc = AgentInactivityTimeoutError(current_agent, 30.0, opts=opts)
    state, _effects, _failure_evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent=current_agent),
    )

    chain = state.chain_for_phase("development")
    assert chain is not None
    available = controller.agents_now_available("development", chain.agents)
    assert "claude" in available, (
        f"after advancing past claude's 15s base cooldown, claude must "
        f"be available, got available={available!r}"
    )
    assert chain.current_index == 0, (
        f"controller must route to the FIRST available agent in chain "
        f"order (preferred agent selection in preferred_agent_index), "
        f"got current_index={chain.current_index}, available={available!r}"
    )


def test_prompt_scenario_session_scope_with_future_expansion_seam() -> None:
    guard_module = importlib.import_module(
        "tests.pipeline.test_run_loop_waiting_state_real_controller"
    )
    guard_test = getattr(
        guard_module,
        "test_run_loop_does_not_reach_through_private_tracker_attributes",
        None,
    )
    assert callable(guard_test), (
        "the dedicated private-reach-through guard test must remain "
        "present in tests/pipeline/test_run_loop_waiting_state_real_controller.py"
    )

    marker: dict[str, object] = {}
    entries: dict[str, UnavailabilityEntry] = {}

    class _CustomStore:
        @property
        def scope(self) -> str:
            return "session"

        def mark_unavailable(
            self,
            phase: str,
            agent: str,
            reason: UnavailabilityReason | None = None,
        ) -> UnavailabilityEntry:
            marker["last_call"] = (phase, agent, reason)
            key = agent
            entry = UnavailabilityEntry(
                unavailable_until_ms=10_000,
                reason=reason,
                attempt=0,
                base_backoff_ms=5_000,
                max_backoff_ms=30_000,
            )
            entries[key] = entry
            return entry

        def is_available(self, phase: str, agent: str) -> bool:
            del phase
            return agent not in entries

        def earliest_unavailable_wait_ms(self, phase: str, agents: list[str]) -> int:
            return 0

        def reset_backoff(self, phase: str, agent: str) -> None:
            del phase
            entries.pop(agent, None)

        def snapshot(self) -> dict[str, dict[str, object]]:
            unavailable_timeouts: dict[str, int] = {
                key: entry.unavailable_until_ms for key, entry in entries.items()
            }
            backoff_attempts: dict[str, int] = dict.fromkeys(entries, 1)
            return {
                "unavailable_timeouts": unavailable_timeouts,
                "backoff_attempts": backoff_attempts,
            }

    custom_store = _CustomStore()
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
            unavailability_store=custom_store,
        ),
    )

    assert controller.unavailability_store is custom_store
    assert isinstance(controller.unavailability_store, UnavailabilityStore) is True

    default_tracker = AgentUnavailabilityTracker()
    assert default_tracker.scope == "session"

    state = _three_agent_state()
    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts())
    state, _effects, _failure_evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    assert marker.get("last_call") == (
        "development",
        "claude",
        UnavailabilityReason.NO_OUTPUT_AT_START,
    ), (
        f"custom store's mark_unavailable must be called with the right "
        f"(phase, agent, reason), got {marker.get('last_call')!r}"
    )

    payload = controller.waiting_state_payload(
        "development",
        ["claude", "opencode", "agy"],
    )
    assert isinstance(payload, list)
    assert len(payload) == 3
    agents_in_payload = [tup[0] for tup in payload]
    assert agents_in_payload == ["claude", "opencode", "agy"], (
        f"waiting_state_payload must preserve chain order, got {agents_in_payload!r}"
    )
    for tup in payload:
        assert isinstance(tup, tuple)
        assert len(tup) == 3
        agent_name, attempt, cooldown_ms = tup
        assert isinstance(agent_name, str)
        assert isinstance(attempt, int)
        assert attempt >= 0
        assert isinstance(cooldown_ms, int)
        assert cooldown_ms >= 0, (
            f"cooldown_ms_remaining must be non-negative, got {cooldown_ms}ms "
            f"for agent {agent_name!r}"
        )

    available = controller.agents_now_available(
        "development",
        ["claude", "opencode", "agy"],
    )
    assert "opencode" in available
    assert "agy" in available
    assert "claude" not in available, (
        f"claude was marked unavailable, must NOT appear in agents_now_available, got {available!r}"
    )

    snapshot = controller.snapshot()
    assert "unavailable_timeouts" in snapshot
    assert "backoff_attempts" in snapshot
    assert snapshot["unavailable_timeouts"] == {"claude": 10_000}
