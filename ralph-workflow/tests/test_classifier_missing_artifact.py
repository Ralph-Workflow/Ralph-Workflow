"""Tests: missing-required-artifact failure strings classify as AMBIGUOUS."""

from __future__ import annotations

import pytest

from ralph.recovery.classifier import (
    FailureCategory,
    FailureClassifier,
    _is_missing_artifact_message,
)

_CLASSIFIER = FailureClassifier()
_PHASE = "review"
_AGENT = "claude"


@pytest.mark.parametrize(
    "message",
    [
        "Missing required artifact at .agent/artifacts/issues.json",
        "Artifact not found at .agent/artifacts/fix_result.json",
        "required_artifact_missing: plan artifact not submitted",
        "Missing/invalid issues artifact: Artifact not found at .agent/artifacts/issues.json",
        "Missing required analysis artifact at .agent/artifacts/development_analysis_decision.json",
        "Missing fix_result artifact at .agent/artifacts/fix_result.json",
    ],
)
def test_missing_artifact_message_classifies_as_ambiguous(message: str) -> None:
    result = _CLASSIFIER.classify(message, phase=_PHASE, agent=_AGENT)
    assert result.category == FailureCategory.AMBIGUOUS, (
        f"Expected AMBIGUOUS for message {message!r}, got {result.category}"
    )


@pytest.mark.parametrize(
    "message",
    [
        "Missing required artifact at .agent/artifacts/issues.json",
        "Artifact not found at .agent/artifacts/plan.json",
        "required_artifact_missing",
        "Missing/invalid issues artifact: file not found",
        "Missing required analysis artifact at .agent/artifacts/review_analysis_decision.json",
        "Missing fix_result artifact at .agent/artifacts/fix_result.json",
    ],
)
def test_is_missing_artifact_message_helper(message: str) -> None:
    assert _is_missing_artifact_message(message), (
        f"Expected _is_missing_artifact_message to return True for {message!r}"
    )


def test_missing_artifact_does_not_count_against_budget() -> None:
    msg = "Missing required artifact at .agent/artifacts/issues.json"
    result = _CLASSIFIER.classify(msg, phase=_PHASE, agent=_AGENT)
    assert not result.counts_against_budget


def test_non_artifact_failure_still_classifies() -> None:
    result = _CLASSIFIER.classify("some unrelated error", phase=_PHASE, agent=_AGENT)
    assert result.category == FailureCategory.AMBIGUOUS
