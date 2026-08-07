"""Black-box tests for hermetic verification-prompt evaluation scoring."""

from __future__ import annotations

import pytest

from ralph.testing.verification_prompt_evaluation import EvaluationCase, score_decisions


def _decision(verdict: str, evidence: str, location: str) -> dict[str, object]:
    return {
        "criterion_verdict_ids": ["DA-001"],
        "criterion_verdicts": [
            "Criterion: behavior holds. Expected observation: focused evidence observes it. "
            f"Verdict: {verdict}. Evidence: {evidence} Location: {location}."
        ],
    }


def test_scoring_counts_localized_planted_defects() -> None:
    case = EvaluationCase("defect", frozenset({"DA-001"}), frozenset({"src/example.py:10"}))

    metrics = score_decisions(case, [_decision("not met", "failure output. ", "src/example.py:10")])

    assert metrics["localized_defect_recall"] == 1.0
    assert metrics["false_rejection_rate"] == 0.0


def test_scoring_counts_false_rejection_and_unsupported_met() -> None:
    case = EvaluationCase("correct", frozenset({"DA-001"}), frozenset())

    metrics = score_decisions(
        case,
        [
            _decision("not met", "failure output. ", "src/example.py:10"),
            _decision("met", "", "src/example.py:10"),
        ],
    )

    assert metrics["false_rejection_rate"] == 0.5
    assert metrics["unsupported_met_rate"] == 1.0


def test_scoring_reports_repeated_run_disagreement() -> None:
    case = EvaluationCase("correct", frozenset({"DA-001"}), frozenset())

    metrics = score_decisions(
        case,
        [
            _decision("met", "test output. ", "src/example.py:10"),
            _decision("not met", "failure output. ", "src/example.py:10"),
        ],
    )

    assert metrics["verdict_disagreement_rate"] == 1.0


def test_scoring_rejects_unvalidated_decision_shape() -> None:
    case = EvaluationCase("correct", frozenset({"DA-001"}), frozenset())

    with pytest.raises(ValueError, match="production-validator content"):
        score_decisions(case, [{}])
