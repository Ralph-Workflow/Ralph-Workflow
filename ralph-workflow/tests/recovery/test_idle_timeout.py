from __future__ import annotations

from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.recovery.classifier import FailureCategory, FailureClassifier
from ralph.recovery.controller import FailureContext, RecoveryController, RecoveryControllerOptions


def _make_state(agents: list[str]) -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=agents, current_index=0, retries=0)},
    )


class _AgentInactivityTimeoutError(Exception):
    pass


_AgentInactivityTimeoutError.__name__ = "AgentInactivityTimeoutError"


def test_inactivity_timeout_classified_as_agent_fault() -> None:
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


def test_inactivity_timeout_is_charged_against_the_agent() -> None:
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))
    state = _make_state(["claude"])

    for _attempt in range(3):
        _, _, evt = controller.handle(
            state,
            _AgentInactivityTimeoutError("agent idle"),
            FailureContext(phase="development", agent="claude"),
        )
        assert evt.counted_against_budget is True
        assert evt.category == "agent"

    assert controller.ledger.is_available("development", "claude") is False
    assert controller.agents_now_available("development", ["claude"]) == []


def test_network_timeout_is_environmental_not_agent_fault() -> None:
    classifier = FailureClassifier()
    failure = classifier.classify(
        TimeoutError("connection timed out"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.ENVIRONMENTAL
    assert failure.counts_against_budget is False
