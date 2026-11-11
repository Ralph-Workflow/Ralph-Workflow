from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.agents.invoke._errors import AgentInvocationError
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.classifier import FailureCategory, FailureClassifier
from ralph.recovery.controller import (
    FailureContext,
    RecoveryController,
    RecoveryControllerOptions,
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


_CONSECUTIVE_UNPRODUCTIVE_LIMIT = 3


class _AgentTimeoutError(Exception):
    pass


_AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"


def test_agent_fault_is_charged_to_the_agent() -> None:
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))
    state = _make_state(["claude"])

    _, _, evt = controller.handle(
        state,
        _AgentTimeoutError("agent timed out with no output"),
        FailureContext(phase="development", agent="claude"),
    )

    assert evt.category == "agent"
    assert evt.counted_against_budget is True
    assert controller.ledger.is_available("development", "claude") is True


def test_agent_fault_causes_fallover_when_the_agent_is_demoted() -> None:
    collected_fallovers: list[FalloverEvent] = []

    bus = FailureEventBus()
    bus.subscribe(
        lambda evt: collected_fallovers.append(evt) if isinstance(evt, FalloverEvent) else None
    )

    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10, event_bus=bus))
    state = _make_state(["claude", "opencode"])

    new_state = state
    for _attempt in range(_CONSECUTIVE_UNPRODUCTIVE_LIMIT):
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
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, policy_bundle=_minimal_policy_bundle())
    )
    state = _make_state(["claude"])

    new_state = state
    for _attempt in range(_CONSECUTIVE_UNPRODUCTIVE_LIMIT):
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


def test_unavailable_agent_message_classified_with_is_unavailable_flag() -> None:
    classifier = FailureClassifier()

    unavailable_failure = classifier.classify(
        AgentInvocationError("claude", 1, "agent produced no output for 60s"),
        phase="development",
        agent="claude",
        connectivity_state="online",
    )
    assert unavailable_failure.category == FailureCategory.AGENT
    assert unavailable_failure.is_unavailable is True

    network_failure = classifier.classify(
        AgentInvocationError("claude", 1, "Connection reset by peer"),
        phase="development",
        agent="claude",
        connectivity_state="online",
    )
    assert network_failure.category == FailureCategory.ENVIRONMENTAL
    assert network_failure.is_unavailable is False


def test_empty_response_in_parsed_output_classified_as_agent_fault() -> None:
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
