"""Black-box tests for hermetic verification-prompt evaluation scoring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.testing.verification_prompt_evaluation import (
    EvaluationCase,
    load_evaluation_cases,
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


def _markdown(artifact_type: str, verdict: str, evidence: str, location: str) -> str:
    prefix = {"planning_analysis_decision": "PA", "development_analysis_decision": "DA", "policy_remediation_analysis_decision": "PR"}[artifact_type]
    status = "completed" if verdict == "met" else "request_changes"
    target = "Step: [S-1] " if artifact_type == "planning_analysis_decision" else ""
    verdict_item = (
        f"- [{prefix}-001] {target}Criterion: behavior holds. Expected observation: focused evidence observes it. "
        f"Verdict: {verdict}. Evidence: {evidence} Location: {location}."
    )
    shortfall = "" if verdict == "met" else f"\n## What Came Up Short\n\n{verdict_item}\n"
    return f"""---
type: {artifact_type}
status: {status}
---
## Summary
- [SUM-1] Evaluation result.
{shortfall}
## Criterion Verdicts
{verdict_item}
"""


def _case_payload(*, case_id: str = "development", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "case_id": case_id,
        "artifact_type": "development_analysis_decision",
        "template_name": "development_analysis",
        "criterion_ids": ["DA-001"],
        "defect_locations": ["src/example.py:10"],
        "request": "# Request\n\nKeep behavior.",
        "plan_or_policy": "# Plan\n\n## Work\n\n### [S-1] Preserve behavior",
        "workspace_files": {"src/example.py": "value = 1\n"},
        "expected_artifact_type": "development_analysis_decision",
    }
    payload.update(overrides)
    return payload


def _write_cases(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_evaluation_cases_loads_complete_frozen_fixture(tmp_path: Path) -> None:
    cases = load_evaluation_cases(_write_cases(tmp_path, [_case_payload()]))

    assert cases == (
        EvaluationCase(
            case_id="development",
            criterion_ids=frozenset({"DA-001"}),
            defect_locations=frozenset({"src/example.py:10"}),
            artifact_type="development_analysis_decision",
            template_name="development_analysis",
            request="# Request\n\nKeep behavior.",
            plan_or_policy="# Plan\n\n## Work\n\n### [S-1] Preserve behavior",
            workspace_files={"src/example.py": "value = 1\n"},
        ),
    )


@pytest.mark.parametrize(
    "override",
    (
        {"unexpected": True},
        {"template_name": "review_analysis"},
        {"artifact_type": "review_analysis_decision"},
        {"case_id": "../unsafe"},
        {"workspace_files": {"../outside.py": "x"}},
    ),
)
def test_load_evaluation_cases_rejects_malformed_case(tmp_path: Path, override: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        load_evaluation_cases(_write_cases(tmp_path, [_case_payload(**override)]))


def test_load_evaluation_cases_rejects_duplicate_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        load_evaluation_cases(_write_cases(tmp_path, [_case_payload(), _case_payload()]))


def test_load_evaluation_cases_consumes_the_frozen_campaign_fixture() -> None:
    fixture = Path(__file__).parent / "fixtures" / "verification_prompt_evaluation" / "cases.json"

    cases = load_evaluation_cases(fixture)

    assert {case.case_id for case in cases} == {
        "planning-correct",
        "development-planted-defect",
        "policy-planted-defect",
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


def test_runner_uses_distinct_fixture_workspace_per_agent_run(tmp_path: Path) -> None:
    case = load_evaluation_cases(_write_cases(tmp_path, [_case_payload()]))[0]
    observed: list[Path] = []

    def invoke(
        _agent: tuple[str, str], _prompt: str, workspace: Path, _case: EvaluationCase
    ) -> str:
        observed.append(workspace)
        assert (workspace / "src/example.py").read_text(encoding="utf-8") == "value = 1\n"
        assert not (workspace / "undeclared.txt").exists()
        return _markdown("development_analysis_decision", "not met", "failure output.", "src/example.py:10")

    run_evaluation((case,), (("strongest", "provider/strong"),), invoke, runs_per_agent=2)

    assert len(set(observed)) == 2
    assert all(not path.exists() for path in observed)


def test_runner_maps_all_verification_phases_through_production_contract(tmp_path: Path) -> None:
    cases = load_evaluation_cases(
        _write_cases(
            tmp_path,
            [
                _case_payload(
                    case_id="planning",
                    artifact_type="planning_analysis_decision",
                    template_name="planning_analysis",
                    criterion_ids=["PA-001"],
                    defect_locations=[],
                    expected_artifact_type="planning_analysis_decision",
                ),
                _case_payload(case_id="development"),
                _case_payload(
                    case_id="policy",
                    artifact_type="policy_remediation_analysis_decision",
                    template_name="policy_remediation_analysis",
                    criterion_ids=["PR-001"],
                    expected_artifact_type="policy_remediation_analysis_decision",
                ),
            ],
        )
    )

    results = run_evaluation(
        cases,
        (("strongest", "provider/strong"),),
        lambda _agent, prompt, _workspace, case: _markdown(
            case.artifact_type,
            "met",
            "observed output.",
            "src/example.py:10",
        ),
    )

    assert set(results["strongest"]) == {"planning", "development", "policy"}


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
    case = EvaluationCase("planted-defect", frozenset({"DA-001"}), frozenset({"src/example.py:10"}))
    agents = (("strongest", "provider/strong"), ("weakest", "provider/weak"))

    results = run_evaluation(
        (case,),
        agents,
        lambda agent, _prompt, _workspace, case: _markdown(
            case.artifact_type,
            "not met" if agent[0] == "strongest" else "met",
            "failure output.",
            "src/example.py:10",
        ),
    )

    assert results["strongest"]["planted-defect"]["localized_defect_recall"] == 1.0
    assert results["weakest"]["planted-defect"]["localized_defect_recall"] == 0.0


def test_scoring_rejects_unvalidated_decision_shape() -> None:
    case = EvaluationCase("correct", frozenset({"DA-001"}), frozenset())

    with pytest.raises(ValueError, match="production-validator content"):
        score_decisions(case, [{}])
