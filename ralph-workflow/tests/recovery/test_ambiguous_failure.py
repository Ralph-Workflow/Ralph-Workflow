"""Black-box test: ambiguous failures are classified correctly and flagged for review."""

from __future__ import annotations

import io

from loguru import logger

from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.classifier import FailureCategory, FailureClassifier
from ralph.recovery.controller import RecoveryController


def _make_state(agents: list[str] | None = None) -> PipelineState:
    if agents is None:
        agents = ["claude"]
    return PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=agents, current_index=0, retries=0)},
    )


def test_unknown_exception_is_ambiguous() -> None:
    """An exception that doesn't match known patterns is classified as AMBIGUOUS."""
    classifier = FailureClassifier()

    failure = classifier.classify(
        RuntimeError("something went wrong but not sure what"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.AMBIGUOUS
    assert failure.counts_against_budget is False


def test_generic_exception_message_ambiguous() -> None:
    """Generic error messages that don't match transport patterns are ambiguous."""
    classifier = FailureClassifier()

    failure = classifier.classify(
        ValueError("invalid input provided"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.AMBIGUOUS
    assert failure.counts_against_budget is False


def test_ambiguous_failure_does_not_debit_budget() -> None:
    """Ambiguous failures must not count against the agent budget."""
    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3)
    controller = RecoveryController(cycle_cap=10, budget_registry=registry)
    state = _make_state()

    _, _, evt = controller.handle(
        state,
        RuntimeError("something unexpected happened"),
        phase="development",
        agent="claude",
    )

    assert evt.counted_against_budget is False
    assert evt.category == "ambiguous"

    # Budget should not be debited
    budget_state = controller.budget_registry.get("development", "claude")
    assert budget_state is not None
    assert budget_state.consumed == 0


def test_ambiguous_failure_returns_state_without_phase_change() -> None:
    """Ambiguous failures keep the pipeline running in the same phase."""
    controller = RecoveryController(cycle_cap=10)
    state = _make_state()

    new_state, effects, evt = controller.handle(
        state,
        OSError("some system error"),
        phase="development",
        agent="claude",
    )

    assert new_state.phase == "development"
    assert effects == []  # No exit effect
    assert evt.counted_against_budget is False


def test_ambiguous_failure_is_flagged_in_reason() -> None:
    """Ambiguous failure reason includes the flagged_for_review indication."""
    classifier = FailureClassifier()

    failure = classifier.classify(
        Exception("an exception without clear attribution"),
        phase="development",
        agent="claude",
    )

    assert FailureCategory.AMBIGUOUS in failure.reason or "flagged" in failure.reason.lower()


def test_ambiguous_failure_emits_warning_log() -> None:
    """Ambiguous failures emit a warning log flagged for review."""
    sink = io.StringIO()
    handler_id = logger.add(sink, level="WARNING", format="{level} {message}")
    try:
        classifier = FailureClassifier()
        failure = classifier.classify(
            RuntimeError("unrelated failure"),
            phase="development",
            agent="claude",
        )
        assert failure.category == FailureCategory.AMBIGUOUS
        assert failure.counts_against_budget is False
        log_output = sink.getvalue()
        assert "flagged_for_review" in log_output.lower() or "ambiguous" in log_output.lower()
    finally:
        logger.remove(handler_id)


def test_artifact_validation_failure_is_not_flagged_as_ambiguous() -> None:
    sink = io.StringIO()
    handler_id = logger.add(sink, level="WARNING", format="{level} {message}")
    try:
        classifier = FailureClassifier()
        failure = classifier.classify(
            "PROOF INCOMPLETE: The following how_to_fix item(s) have no proof entry: ['Add test']",
            phase="development",
            agent="claude",
        )
        assert failure.category == FailureCategory.ARTIFACT_VALIDATION
        assert "flagged_for_review" not in sink.getvalue().lower()
    finally:
        logger.remove(handler_id)


def test_connection_refused_is_not_ambiguous() -> None:
    """ConnectionRefused is clearly environmental, not ambiguous."""
    classifier = FailureClassifier()

    failure = classifier.classify(
        ConnectionRefusedError("connection refused"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.ENVIRONMENTAL
    assert failure.counts_against_budget is False


def test_timeout_error_is_environmental() -> None:
    """TimeoutError is environmental, not ambiguous."""
    classifier = FailureClassifier()

    failure = classifier.classify(
        TimeoutError("operation timed out"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.ENVIRONMENTAL
    assert failure.counts_against_budget is False


def test_agent_inactivity_timeout_is_agent_fault() -> None:
    """AgentInactivityTimeoutError is agent fault, not ambiguous."""
    classifier = FailureClassifier()

    class AgentInactivityTimeoutError(Exception):
        pass

    AgentInactivityTimeoutError.__name__ = "AgentInactivityTimeoutError"

    failure = classifier.classify(
        AgentInactivityTimeoutError("agent idle for too long"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.AGENT
    assert failure.counts_against_budget is True
