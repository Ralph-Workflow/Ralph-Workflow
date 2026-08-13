"""Focused tests for analysis-decision Markdown contracts."""

from __future__ import annotations

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec


def _decision(shortfall: str) -> str:
    return f"""---
type: planning_analysis_decision
status: request_changes
---
## Summary
- [SUM-1] The plan needs correction.
## What Came Up Short
- [PA-001] {shortfall} Criterion: verification is runnable. Expected observation: the command resolves. Verdict: not met. Evidence: command output. Location: plan step.
## Criterion Verdicts
- [PA-001] Step: [S-2] Criterion: verification is runnable. Expected observation: the command resolves. Verdict: not met. Evidence: command output. Location: plan step.
"""


def test_request_changes_requires_a_step_or_plan_level_target() -> None:
    content, diagnostics = parse_and_validate(
        _decision("The rollout risk is unaddressed."),
        get_spec("planning_analysis_decision"),
    )

    assert content == {}
    assert [(item.rule_id, item.severity) for item in diagnostics] == [("ANALYSIS004", "error")]


def test_non_planning_request_changes_do_not_require_a_plan_step_target() -> None:
    document = _decision("The implementation omits a required negative test.").replace(
        "planning_analysis_decision", "development_analysis_decision"
    ).replace("PA-001", "DA-001").replace(
        "Location: plan step.\n## Criterion",
        "Location: src/example.py:1. Remaining work: add the missing negative test.\n## Criterion",
    )

    content, diagnostics = parse_and_validate(document, get_spec("development_analysis_decision"))

    assert diagnostics == []
    assert content["finding_targets"] == {}


def test_request_changes_preserves_exact_finding_binding() -> None:
    content, diagnostics = parse_and_validate(
        _decision("Step: [S-2] lacks an executable verification command."),
        get_spec("planning_analysis_decision"),
    )

    assert diagnostics == []
    assert content["finding_targets"] == {"PA-001": "S-2"}
    assert content["finding_ids"] == ["PA-001"]


def test_verification_decision_rejects_a_finding_without_evidence_fields() -> None:
    document = _decision("Step: [S-2] lacks evidence.").replace(
        " Criterion: verification is runnable. Expected observation: the command resolves. Verdict: not met. Evidence: command output. Location: plan step.",
        "",
    )

    content, diagnostics = parse_and_validate(document, get_spec("planning_analysis_decision"))

    assert content == {}
    assert [(item.rule_id, item.severity) for item in diagnostics] == [
        ("ANALYSIS005", "error"),
        ("ANALYSIS005", "error"),
    ]

def test_failed_planning_decision_requires_a_step_or_plan_level_target() -> None:
    document = _decision("The rollout risk is unaddressed.").replace(
        "status: request_changes", "status: failed"
    )

    content, diagnostics = parse_and_validate(document, get_spec("planning_analysis_decision"))

    assert content == {}
    assert [(item.rule_id, item.severity) for item in diagnostics] == [("ANALYSIS004", "error")]


def test_criterion_verdict_rejects_non_numeric_phase_id() -> None:
    document = """---
type: development_analysis_decision
status: completed
---
## Summary
- [SUM-1] No counterexample was found.

## Criterion Verdicts
- [DA-invalid] Criterion: the public API remains available. Expected observation: the module exports the API. Verdict: met. Evidence: src/api.py:10. Location: src/api.py:10.
"""

    content, diagnostics = parse_and_validate(document, get_spec("development_analysis_decision"))

    assert content == {}
    assert [(item.rule_id, item.severity) for item in diagnostics] == [("ANALYSIS010", "error")]


def test_completed_verification_decision_requires_evidence_citing_criterion_verdicts() -> None:
    document = """---
type: development_analysis_decision
status: completed
---
## Summary
- [SUM-1] No counterexample was found.

## Criterion Verdicts
- [DA-001] Criterion: the public API remains available. Expected observation: the module exports the API. Verdict: met. Evidence: src/api.py:10. Location: src/api.py:10.
"""

    content, diagnostics = parse_and_validate(document, get_spec("development_analysis_decision"))

    assert diagnostics == []
    assert content["criterion_verdict_ids"] == ["DA-001"]


def test_completed_verification_decision_rejects_non_met_verdict() -> None:
    document = """---
type: development_analysis_decision
status: completed
---
## Summary
- [SUM-1] No counterexample was found.

## Criterion Verdicts
- [DA-001] Criterion: the public API remains available. Expected observation: the module exports the API. Verdict: not met. Evidence: src/api.py:10. Location: src/api.py:10.
"""

    content, diagnostics = parse_and_validate(document, get_spec("development_analysis_decision"))

    assert content == {}
    assert [(item.rule_id, item.severity) for item in diagnostics] == [("ANALYSIS012", "error")]


def test_completed_verification_decision_rejects_missing_criterion_verdicts() -> None:
    document = """---
type: development_analysis_decision
status: completed
---
## Summary
- [SUM-1] No counterexample was found.
"""

    content, diagnostics = parse_and_validate(document, get_spec("development_analysis_decision"))

    assert content == {}
    assert [(item.rule_id, item.severity) for item in diagnostics] == [("ANALYSIS006", "error")]


def test_criterion_verdict_rejects_unsupported_verdict_or_uncited_met() -> None:
    document = """---
type: policy_remediation_analysis_decision
status: completed
---
## Summary
- [SUM-1] No counterexample was found.

## Criterion Verdicts
- [PR-001] Criterion: the command resolves. Expected observation: make runs it. Verdict: passed. Evidence: command output. Location: policy.md:10.
- [PR-002] Criterion: the fact is current. Expected observation: the location matches. Verdict: met. Location: policy.md:11.
"""

    content, diagnostics = parse_and_validate(document, get_spec("policy_remediation_analysis_decision"))

    assert content == {}
    assert [item.rule_id for item in diagnostics] == ["ANALYSIS008", "ANALYSIS005"]


def test_criterion_verdict_rejects_empty_evidence_for_met() -> None:
    document = """---
type: development_analysis_decision
status: completed
---
## Summary
- [SUM-1] No counterexample was found.

## Criterion Verdicts
- [DA-001] Criterion: the public API remains available. Expected observation: the module exports the API. Verdict: met. Evidence: Location: src/api.py:10.
"""

    content, diagnostics = parse_and_validate(document, get_spec("development_analysis_decision"))

    assert content == {}
    assert [(item.rule_id, item.severity) for item in diagnostics] == [("ANALYSIS009", "error")]


def test_not_evaluable_criterion_verdict_requires_failed_status() -> None:
    document = """---
type: planning_analysis_decision
status: request_changes
---
## Summary
- [SUM-1] Evidence is unavailable.

## What Came Up Short
- [PA-001] Plan-level: Criterion: the plan is runnable. Expected observation: the command resolves. Verdict: not met. Evidence: command unavailable. Location: plan.

## Criterion Verdicts
- [PA-001] Plan-level: Criterion: the plan is runnable. Expected observation: the command resolves. Verdict: not evaluable. Evidence: command unavailable. Location: plan.
"""

    content, diagnostics = parse_and_validate(document, get_spec("planning_analysis_decision"))

    assert content == {}
    assert [(item.rule_id, item.severity) for item in diagnostics] == [("ANALYSIS007", "error"), ("ANALYSIS018", "error")]


def test_not_evaluable_verdict_requires_failed_status() -> None:
    document = _decision("Step: [S-2] cannot be observed.").replace(
        "Verdict: not met", "Verdict: not evaluable"
    )

    content, diagnostics = parse_and_validate(document, get_spec("planning_analysis_decision"))

    assert content == {}
    assert [(item.rule_id, item.severity) for item in diagnostics] == [
        ("ANALYSIS007", "error"),
        ("ANALYSIS007", "error"),
    ]


def test_verification_decision_rejects_empty_location() -> None:
    document = """---
type: development_analysis_decision
status: completed
---
## Summary
- [SUM-1] No counterexample was found.

## Criterion Verdicts
- [DA-001] Criterion: the API is available. Expected observation: the export exists. Verdict: met. Evidence: src/api.py:10. Location:
"""

    content, diagnostics = parse_and_validate(document, get_spec("development_analysis_decision"))

    assert content == {}
    assert [(item.rule_id, item.severity) for item in diagnostics] == [("ANALYSIS013", "error")]


def test_verification_decision_requires_each_non_met_verdict_to_be_mirrored() -> None:
    document = """---
type: development_analysis_decision
status: request_changes
---
## Summary
- [SUM-1] One fixed criterion is not met.

## What Came Up Short
- [DA-002] Criterion: another behavior holds. Expected observation: focused evidence observes it. Verdict: not met. Evidence: output. Location: src/example.py:11. Remaining work: implement the missing behavior.

## Criterion Verdicts
- [DA-001] Criterion: behavior holds. Expected observation: focused evidence observes it. Verdict: not met. Evidence: output. Location: src/example.py:10.
"""

    content, diagnostics = parse_and_validate(document, get_spec("development_analysis_decision"))

    assert content == {}
    assert [item.rule_id for item in diagnostics] == ["ANALYSIS014", "ANALYSIS014"]


def test_request_changes_allows_explicit_plan_level_target() -> None:
    content, diagnostics = parse_and_validate(
        _decision("Plan-level: The outcome omits an out-of-scope boundary."),
        get_spec("planning_analysis_decision"),
    )

    assert diagnostics == []
    assert content["finding_targets"] == {"PA-001": "plan-level"}
