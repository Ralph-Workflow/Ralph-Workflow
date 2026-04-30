"""Black-box test: agent empty output is attributed to the agent and debits budget."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.config.enums import PHASE_DEVELOPMENT, PHASE_FAILED
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.classifier import FailureCategory, FailureClassifier
from ralph.recovery.controller import RecoveryController
from ralph.recovery.events import FailureEventBus, FalloverEvent


def _minimal_policy_bundle():
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _make_state(agents: list[str]) -> PipelineState:
    return PipelineState(
        phase=PHASE_DEVELOPMENT,
        dev_chain=AgentChainState(agents=agents, current_index=0, retries=0),
    )


def _make_registry_with_budget(phase: str, agent: str, max_retries: int = 1) -> AgentBudgetRegistry:
    return AgentBudgetRegistry().set_budget(phase, agent, max_retries)


def test_agent_fault_debits_budget() -> None:
    """An agent-attributable fault decrements the budget."""
    registry = _make_registry_with_budget(PHASE_DEVELOPMENT, "claude", max_retries=2)
    controller = RecoveryController(cycle_cap=10, budget_registry=registry)
    state = _make_state(["claude"])

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    _, _, evt = controller.handle(
        state,
        _AgentTimeoutError("agent timed out with no output"),
        phase=PHASE_DEVELOPMENT,
        agent="claude",
    )

    assert evt.category == "agent"
    assert evt.counted_against_budget is True
    state_after = controller.budget_registry.get(PHASE_DEVELOPMENT, "claude")
    assert state_after is not None
    assert state_after.consumed == 1


def test_agent_fault_causes_fallover_when_budget_exhausted() -> None:
    """When agent budget is exhausted, controller falls over to next agent."""
    registry = _make_registry_with_budget(PHASE_DEVELOPMENT, "claude", max_retries=1)
    collected_fallovers: list[FalloverEvent] = []

    bus = FailureEventBus()
    bus.subscribe(
        lambda evt: collected_fallovers.append(evt)  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        if isinstance(evt, FalloverEvent)
        else None
    )

    controller = RecoveryController(cycle_cap=10, budget_registry=registry, event_bus=bus)
    state = _make_state(["claude", "opencode"])

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    new_state, _, _ = controller.handle(
        state,
        _AgentTimeoutError("agent idle timeout"),
        phase=PHASE_DEVELOPMENT,
        agent="claude",
    )

    assert len(collected_fallovers) == 1
    assert collected_fallovers[0].from_agent == "claude"
    assert collected_fallovers[0].to_agent == "opencode"
    assert new_state.chain_for_phase("development").current_index == 1
    assert len(new_state.fallover_history) == 1


def test_agent_fault_chain_exhaustion_enters_phase_failed() -> None:
    """When entire chain is exhausted, state enters PHASE_FAILED."""
    registry = _make_registry_with_budget(PHASE_DEVELOPMENT, "claude", max_retries=1)
    controller = RecoveryController(
        cycle_cap=10, budget_registry=registry, policy_bundle=_minimal_policy_bundle()
    )
    state = _make_state(["claude"])

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    new_state, _, _ = controller.handle(
        state,
        _AgentTimeoutError("agent idle timeout"),
        phase=PHASE_DEVELOPMENT,
        agent="claude",
    )

    assert new_state.phase == PHASE_FAILED
    assert new_state.recovery_cycle_count == 1
    assert new_state.last_error is not None
    assert (
        "agent" in new_state.last_error.lower() or "timeout" in new_state.last_error.lower()
    )


def test_empty_output_message_classified_as_agent_fault() -> None:
    """Messages about empty output are classified as agent faults."""
    classifier = FailureClassifier()

    class FakeInvocationError(Exception):
        pass

    FakeInvocationError.__name__ = "AgentInvocationError"

    failure = classifier.classify(
        FakeInvocationError("agent produced empty output, no progress"),
        phase=PHASE_DEVELOPMENT,
        agent="claude",
    )

    assert failure.category == FailureCategory.AGENT
    assert failure.counts_against_budget is True
