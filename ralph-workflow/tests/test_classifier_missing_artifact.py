"""Tests: artifact/proof validation strings classify as artifact_validation."""

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
def test_missing_artifact_message_classifies_as_artifact_validation(message: str) -> None:
    result = _CLASSIFIER.classify(message, phase=_PHASE, agent=_AGENT)
    assert result.category == FailureCategory.ARTIFACT_VALIDATION, (
        f"Expected ARTIFACT_VALIDATION for message {message!r}, got {result.category}"
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


@pytest.mark.parametrize(
    "message",
    [
        (
            "PROOF INCOMPLETE: The following how_to_fix item(s) have no proof entry: "
            "['Add test']. Each how_to_fix_item must exactly match the prior analysis text."
        ),
        "PROOF INVALID: Duplicate how_to_fix_item entries found in analysis_items_addressed.",
        "Invalid development evidence: Artifact type mismatch: expected plan, got 'wrong'",
    ],
)
def test_proof_and_artifact_validation_messages_classify_as_artifact_validation(
    message: str,
) -> None:
    result = _CLASSIFIER.classify(message, phase=_PHASE, agent=_AGENT)
    assert result.category == FailureCategory.ARTIFACT_VALIDATION
    assert result.counts_against_budget is False


def test_non_artifact_failure_still_classifies() -> None:
    result = _CLASSIFIER.classify("some unrelated error", phase=_PHASE, agent=_AGENT)
    assert result.category == FailureCategory.AMBIGUOUS
