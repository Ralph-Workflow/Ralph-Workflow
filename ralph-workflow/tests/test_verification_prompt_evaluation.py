"""Black-box tests for hermetic verification-prompt evaluation scoring."""

from __future__ import annotations

import pytest

from ralph.testing.verification_prompt_evaluation import (
    EvaluationCase,
    run_evaluation,
    score_decisions,
)


def _decision(verdict: str, evidence: str, location: str) -> dict[str, object]:
    return {
        "criterion_verdict_ids": ["DA-001"],
        "criterion_verdicts": [
            "Criterion: behavior holds. Expected observation: focused evidence observes it. "
            f"Verdict: {verdict}. Evidence: {evidence} Location: {location}."
        ],
    }


def _markdown(verdict: str, evidence: str, location: str) -> str:
    return f"""---
type: development_analysis_decision
status: {'completed' if verdict == 'met' else 'request_changes'}
---
## Summary
- [SUM-1] Evaluation result.

## {'Criterion Verdicts' if verdict == 'met' else 'What Came Up Short'}
- [DA-001] Criterion: behavior holds. Expected observation: focused evidence observes it. Verdict: {verdict}. Evidence: {evidence} Location: {location}.

## Criterion Verdicts
- [DA-001] Criterion: behavior holds. Expected observation: focused evidence observes it. Verdict: {verdict}. Evidence: {evidence} Location: {location}.
""" if verdict != 'met' else f"""---
type: development_analysis_decision
status: completed
---
## Summary
- [SUM-1] Evaluation result.

## Criterion Verdicts
- [DA-001] Criterion: behavior holds. Expected observation: focused evidence observes it. Verdict: met. Evidence: {evidence} Location: {location}.
"""


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


def test_runner_scores_repeated_agent_runs_for_disagreement() -> None:
    case = EvaluationCase("correct", frozenset({"DA-001"}), frozenset())
    calls = 0

    def invoke(_agent: tuple[str, str], _prompt: str, _case: EvaluationCase) -> str:
        nonlocal calls
        calls += 1
        return _markdown(
            "met" if calls == 1 else "not met",
            "test output. ",
            "src/example.py:10",
        )

    results = run_evaluation((case,), (("weakest", "provider/weak"),), invoke, runs_per_agent=2)

    assert results["weakest"]["correct"]["verdict_disagreement_rate"] == 1.0


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


def test_runner_reports_separate_metrics_for_strongest_and_weakest_agents() -> None:
    case = EvaluationCase(
        "planted-defect",
        frozenset({"DA-001"}),
        frozenset({"src/example.py:10"}),
        artifact_type="development_analysis_decision",
        template_name="development_analysis",
    )
    agents = (("strongest", "provider/strong"), ("weakest", "provider/weak"))

    results = run_evaluation(
        (case,),
        agents,
        lambda agent, _prompt, _case: (
            _markdown("not met", "failure output. ", "src/example.py:10")
            if agent[0] == "strongest"
            else _markdown("met", "test output. ", "src/example.py:10")
        ),
    )

    assert results["strongest"]["planted-defect"]["localized_defect_recall"] == 1.0
    assert results["weakest"]["planted-defect"]["localized_defect_recall"] == 0.0


def test_scoring_rejects_unvalidated_decision_shape() -> None:
    case = EvaluationCase("correct", frozenset({"DA-001"}), frozenset())

    with pytest.raises(ValueError, match="production-validator content"):
        score_decisions(case, [{}])
