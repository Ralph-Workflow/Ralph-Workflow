from __future__ import annotations

import pytest

from ralph.recovery.classifier import FailureCategory, FailureClassifier

_CLASSIFIER = FailureClassifier()


@pytest.mark.parametrize(
    "message",
    [
        "Connection error.",
        "pi agent provider failure (stopReason=error): Connection error.",
    ],
)
def test_pi_connection_error_is_environmental(message: str) -> None:
    failure = _CLASSIFIER.classify(
        message, phase="development", agent="pi/codex-pooler/gpt-5.6-terra"
    )

    assert failure.category is FailureCategory.ENVIRONMENTAL, (
        f"an unreachable provider is a transport fault, got {failure.category}"
    )
    assert failure.counts_against_budget is False


def test_ordinary_failure_is_not_environmental() -> None:
    failure = _CLASSIFIER.classify(
        "AssertionError: expected 3 got 4",
        phase="development",
        agent="pi/codex-pooler/gpt-5.6-terra",
    )

    assert failure.category is not FailureCategory.ENVIRONMENTAL
