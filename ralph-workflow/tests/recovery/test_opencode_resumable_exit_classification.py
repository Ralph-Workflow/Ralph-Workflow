from __future__ import annotations

from ralph.agents.invoke._open_code_resumable_exit_error import (
    OpenCodeResumableExitError,
)
from ralph.agents.invoke._session_resume import recovery_action_for_failure_reason
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import FailureClassifier


def test_opencode_resumable_exit_error_classifies_as_agent() -> None:
    exc = OpenCodeResumableExitError(agent_name="opencode", session_id="sess-xyz")
    classifier = FailureClassifier()
    failure = classifier.classify(
        exc,
        phase="development",
        agent="opencode",
        connectivity_state="online",
    )
    assert failure.category == FailureCategory.AGENT
    assert failure.resumable_session_id == "sess-xyz"
    assert failure.counts_against_budget is True
    assert failure.reset_session is False


def test_opencode_resumable_exit_error_does_not_flag_ambiguous() -> None:
    exc = OpenCodeResumableExitError(agent_name="opencode", session_id="sess-amb-1")
    classifier = FailureClassifier()
    failure = classifier.classify(
        exc,
        phase="development",
        agent="opencode",
        connectivity_state="online",
    )
    assert failure.category != FailureCategory.AMBIGUOUS
    assert failure.category == FailureCategory.AGENT


def test_recovery_action_for_resumable_exit_returns_resume() -> None:
    action = recovery_action_for_failure_reason(
        "OpenCodeResumableExitError",
        has_prior_session=True,
    )
    assert action == "resume"


def test_recovery_action_for_resumable_exit_returns_fresh_when_no_prior_session() -> None:
    action = recovery_action_for_failure_reason(
        "OpenCodeResumableExitError",
        has_prior_session=False,
    )
    assert action == "fresh"


def test_opencode_resumable_exit_error_is_not_ambiguous() -> None:
    for session_id in ("sess-a", "sess-b", "sess-c", None):
        exc = OpenCodeResumableExitError(agent_name="opencode", session_id=session_id)
        classifier = FailureClassifier()
        failure = classifier.classify(
            exc,
            phase="development",
            agent="opencode",
            connectivity_state="online",
        )
        assert failure.category == FailureCategory.AGENT, (
            f"OpenCodeResumableExitError with session_id={session_id!r}"
            " must classify as AGENT (not AMBIGUOUS)"
        )
        assert failure.category != FailureCategory.AMBIGUOUS


def test_resumable_session_id_propagates_from_typed_exception() -> None:
    exc = OpenCodeResumableExitError(agent_name="opencode", session_id="sess-typed-attr")
    assert exc.resumable_session_id == "sess-typed-attr"
    classifier = FailureClassifier()
    failure = classifier.classify(
        exc,
        phase="development",
        agent="opencode",
        connectivity_state="online",
    )
    assert failure.resumable_session_id == "sess-typed-attr"
