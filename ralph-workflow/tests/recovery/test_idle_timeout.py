"""Black-box test: idle timeout is classified as an agent fault."""

from __future__ import annotations

from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.classifier import FailureCategory, FailureClassifier
from ralph.recovery.controller import RecoveryController


def _make_state(agents: list[str]) -> PipelineState:
    return PipelineState(
        phase="development",
        dev_chain=AgentChainState(agents=agents, current_index=0, retries=0),
    )


class _AgentInactivityTimeoutError(Exception):
    """Simulates AgentInactivityTimeoutError via class name."""


_AgentInactivityTimeoutError.__name__ = "AgentInactivityTimeoutError"


def test_inactivity_timeout_classified_as_agent_fault() -> None:
    """AgentInactivityTimeoutError is classified as AGENT fault with budget count."""
    classifier = FailureClassifier()
    failure = classifier.classify(
        _AgentInactivityTimeoutError("agent idle for too long"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.AGENT
    assert failure.counts_against_budget is True
    assert failure.attributed_agent == "claude"
    assert failure.attributed_phase == "development"


def test_inactivity_timeout_counts_against_budget_in_controller() -> None:
    """Idle timeout via controller decrements the agent budget."""
    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3)
    controller = RecoveryController(cycle_cap=10, budget_registry=registry)
    state = _make_state(["claude"])

    _, _, evt = controller.handle(
        state,
        _AgentInactivityTimeoutError("agent idle"),
        phase="development",
        agent="claude",
    )

    assert evt.counted_against_budget is True
    assert evt.category == "agent"
    budget = controller.budget_registry.get("development", "claude")
    assert budget is not None
    assert budget.consumed == 1


def test_network_timeout_is_environmental_not_agent_fault() -> None:
    """A network timeout (TimeoutError) is environmental, not agent-attributable."""
    classifier = FailureClassifier()
    failure = classifier.classify(
        TimeoutError("connection timed out"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.ENVIRONMENTAL
    assert failure.counts_against_budget is False
