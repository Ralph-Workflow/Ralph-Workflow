"""Black-box test: agent empty output is attributed to the agent and debits budget."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.agents.invoke._errors import AgentInvocationError
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.classifier import FailureCategory, FailureClassifier
from ralph.recovery.controller import (
    FailureContext,
    RecoveryController,
    RecoveryControllerOptions,
    compute_backoff_ms,
)
from ralph.recovery.events import FailureEventBus, FalloverEvent


def _minimal_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _make_state(agents: list[str]) -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=agents, current_index=0, retries=0)},
    )


def _make_registry_with_budget(phase: str, agent: str, max_retries: int = 1) -> AgentBudgetRegistry:
    return AgentBudgetRegistry().set_budget(phase, agent, max_retries)


def test_agent_fault_debits_budget() -> None:
    """An agent-attributable fault decrements the budget."""
    registry = _make_registry_with_budget("development", "claude", max_retries=2)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, budget_registry=registry)
    )
    state = _make_state(["claude"])

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    _, _, evt = controller.handle(
        state,
        _AgentTimeoutError("agent timed out with no output"),
        FailureContext(phase="development", agent="claude"),
    )

    assert evt.category == "agent"
    assert evt.counted_against_budget is True
    state_after = controller.budget_registry.get("development", "claude")
    assert state_after is not None
    assert state_after.consumed == 1


def test_agent_fault_causes_fallover_when_budget_exhausted() -> None:
    """When agent budget is exhausted, controller falls over to next agent."""
    registry = _make_registry_with_budget("development", "claude", max_retries=1)
    collected_fallovers: list[FalloverEvent] = []

    bus = FailureEventBus()
    bus.subscribe(
        lambda evt: collected_fallovers.append(evt) if isinstance(evt, FalloverEvent) else None
    )

    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, budget_registry=registry, event_bus=bus)
    )
    state = _make_state(["claude", "opencode"])

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    new_state, _, _ = controller.handle(
        state,
        _AgentTimeoutError("agent idle timeout"),
        FailureContext(phase="development", agent="claude"),
    )

    assert len(collected_fallovers) == 1
    assert collected_fallovers[0].from_agent == "claude"
    assert collected_fallovers[0].to_agent == "opencode"
    assert new_state.chain_for_phase("development").current_index == 1
    assert len(new_state.fallover_history) == 1


def test_agent_fault_chain_exhaustion_enters_phase_failed() -> None:
    """When entire chain is exhausted, state enters "failed"."""
    registry = _make_registry_with_budget("development", "claude", max_retries=1)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10, budget_registry=registry, policy_bundle=_minimal_policy_bundle()
        )
    )
    state = _make_state(["claude"])

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    new_state, _, _ = controller.handle(
        state,
        _AgentTimeoutError("agent idle timeout"),
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.phase == "failed_terminal"
    assert new_state.recovery_cycle_count == 1
    assert new_state.last_error is not None
    assert "agent" in new_state.last_error.lower() or "timeout" in new_state.last_error.lower()


def test_empty_output_message_classified_as_agent_fault() -> None:
    """Messages about empty output are classified as agent faults."""
    classifier = FailureClassifier()

    class FakeInvocationError(Exception):
        pass

    FakeInvocationError.__name__ = "AgentInvocationError"

    failure = classifier.classify(
        FakeInvocationError("agent produced empty output, no progress"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.AGENT
    assert failure.counts_against_budget is True


def test_empty_response_in_parsed_output_classified_as_agent_fault() -> None:
    """An empty-response signal carried only in parsed_output (not the primary
    message) must classify as an agent fault, exactly like one in the message.

    Regression for cross-surface drift: the nanocoder/MiniMax-M3 empty turn
    surfaces "Model returned an empty response with no tool calls" in
    parsed_output while stderr carries only the MCP-connect line. The classifier
    must scan the same surface (and shared vocabulary) the retryable reasoner
    uses, so the failure is not silently demoted to AMBIGUOUS.
    """
    exc = AgentInvocationError(
        "nanocoder",
        1,
        "[plain] MCP server connected: ralph",
        parsed_output=[
            "nanocoder raw: reasoning ...",
            "Model returned an empty response with no tool calls",
        ],
    )

    failure = FailureClassifier().classify(exc, phase="development", agent="nanocoder")

    assert failure.category == FailureCategory.AGENT
    assert failure.counts_against_budget is True


def test_online_timeout_with_no_output_debits_budget_in_controller() -> None:
    """Known-online timeout with no output is attributed to the agent budget."""
    registry = _make_registry_with_budget("development", "claude", max_retries=2)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            budget_registry=registry,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _make_state(["claude"]).copy_with(last_connectivity_state="online")

    _, _, evt = controller.handle(
        state,
        "Agent timed out with no output",
        FailureContext(phase="development", agent="claude"),
    )

    assert evt.category == "agent"
    assert evt.counted_against_budget is True
    budget = controller.budget_registry.get("development", "claude")
    assert budget is not None
    assert budget.consumed == 1


def test_unavailable_agent_requires_online_connectivity() -> None:
    """No-output failures are flagged unavailable only with healthy connectivity."""
    classifier = FailureClassifier()
    message = "agent produced no output for 60s"

    for connectivity in ("unknown", "offline"):
        failure = classifier.classify(
            AgentInvocationError("claude", 1, message),
            phase="development",
            agent="claude",
            connectivity_state=connectivity,
        )
        assert failure.category == FailureCategory.AGENT
        assert failure.is_unavailable is False

    online_failure = classifier.classify(
        AgentInvocationError("claude", 1, message),
        phase="development",
        agent="claude",
        connectivity_state="online",
    )
    assert online_failure.category == FailureCategory.AGENT
    assert online_failure.is_unavailable is True

    network_failure = classifier.classify(
        AgentInvocationError("claude", 1, "Connection reset by peer"),
        phase="development",
        agent="claude",
        connectivity_state="online",
    )
    assert network_failure.category == FailureCategory.ENVIRONMENTAL
    assert network_failure.is_unavailable is False


def test_unavailable_agent_skipped_in_recovery_cycle() -> None:
    """An unavailable agent is skipped; it is retried after its timeout expires."""
    clock = FakeClock(start=0.0)
    registry = _make_registry_with_budget("development", "claude", max_retries=2)
    fallovers: list[FalloverEvent] = []
    bus = FailureEventBus()
    bus.subscribe(
        lambda evt: fallovers.append(evt) if isinstance(evt, FalloverEvent) else None
    )
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            budget_registry=registry,
            clock=clock,
            event_bus=bus,
            unavailable_timeouts={"development:claude": 60_000},
        )
    )
    state = _make_state(["claude", "opencode"]).copy_with(
        last_connectivity_state="online"
    )

    new_state, _, evt = controller.handle(
        state,
        AgentInvocationError("claude", 1, "agent produced no output for 60s"),
        FailureContext(phase="development", agent="claude"),
    )

    assert evt.category == "agent"
    assert new_state.chain_for_phase("development").current_index == 1
    assert len(fallovers) == 1
    assert fallovers[0].from_agent == "claude"
    assert fallovers[0].to_agent == "opencode"

    clock.advance(60)
    retry_state = _make_state(["claude", "opencode"]).copy_with(
        last_connectivity_state="online"
    )
    retried_state, _, _ = controller.handle(
        retry_state,
        AgentInvocationError("claude", 1, "transient agent error"),
        FailureContext(phase="development", agent="claude"),
    )
    assert retried_state.chain_for_phase("development").current_index == 0
    assert retried_state.chain_for_phase("development").retries == 1


def test_all_agents_unavailable_triggers_phase_failure() -> None:
    """When every agent in the chain is unavailable, the phase fails."""
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            unavailable_timeouts={
                "development:claude": 60_000,
                "development:opencode": 60_000,
            },
        )
    )
    state = _make_state(["claude", "opencode"]).copy_with(
        last_connectivity_state="online"
    )

    new_state, _, _ = controller.handle(
        state,
        AgentInvocationError("claude", 1, "agent produced no output for 60s"),
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.phase == "failed_terminal"


def test_exponential_backoff_increases_across_cycles() -> None:
    """Consecutive unavailable failures grow the skip timeout exponentially."""
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    msg = "agent produced no output for 60s"

    def _fresh_state() -> PipelineState:
        return _make_state(["claude"]).copy_with(last_connectivity_state="online")

    # First failure: base 5s timeout.
    controller.handle(
        _fresh_state(),
        AgentInvocationError("claude", 1, msg),
        FailureContext(phase="development", agent="claude"),
    )
    snap1 = controller.snapshot()
    assert snap1["unavailable_timeouts"]["development:claude"] == 5_000

    # Second failure before timeout expires: doubled to 10s from now.
    clock.advance(4.9)
    controller.handle(
        _fresh_state(),
        AgentInvocationError("claude", 1, msg),
        FailureContext(phase="development", agent="claude"),
    )
    snap2 = controller.snapshot()
    assert snap2["unavailable_timeouts"]["development:claude"] == 14_900

    # Third failure: doubled again to 20s from now.
    clock.advance(9.9)
    controller.handle(
        _fresh_state(),
        AgentInvocationError("claude", 1, msg),
        FailureContext(phase="development", agent="claude"),
    )
    snap3 = controller.snapshot()
    assert snap3["unavailable_timeouts"]["development:claude"] == 34_800

    # After the timeout expires and backoff is reset, the next mark is base again.
    clock.advance(20.1)
    controller.reset_backoff("development", "claude")
    controller.handle(
        _fresh_state(),
        AgentInvocationError("claude", 1, msg),
        FailureContext(phase="development", agent="claude"),
    )
    snap4 = controller.snapshot()
    assert snap4["unavailable_timeouts"]["development:claude"] == 39_900

    # Public helper caps growth at max_backoff_ms.
    assert compute_backoff_ms(5_000, 0, 300_000) == 5_000
    assert compute_backoff_ms(5_000, 1, 300_000) == 10_000
    assert compute_backoff_ms(5_000, 2, 300_000) == 20_000
    assert compute_backoff_ms(5_000, 10, 300_000) == 300_000
