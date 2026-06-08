"""Black-box test: agent empty output is attributed to the agent and debits budget."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.agents.invoke._errors import AgentInvocationError
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.classifier import FailureCategory, FailureClassifier
from ralph.recovery.controller import FailureContext, RecoveryController, RecoveryControllerOptions
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
    """Known-online timeout with no output should trigger suspicious-agent fallback."""
    registry = _make_registry_with_budget("development", "claude", max_retries=2)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, budget_registry=registry)
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
