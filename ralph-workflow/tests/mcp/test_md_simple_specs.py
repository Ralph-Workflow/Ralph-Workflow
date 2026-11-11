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
- [PA-001] {shortfall} Criterion: verification is runnable. Expected observation: the command resolves. Proposed revision: name the runnable command in the step. Verdict: not met. Evidence: command output. Location: plan step. Cost: re-running the same failing check on the next pass.
## Criterion Verdicts
- [PA-001] Step: [S-2] Criterion: verification is runnable. Expected observation: the command resolves. Proposed revision: name the runnable command in the step. Verdict: not met. Evidence: command output. Location: plan step. Cost: re-running the same failing check on the next pass.
"""


def test_request_changes_requires_a_step_or_plan_level_target() -> None:
    content, diagnostics = parse_and_validate(
        _decision("The rollout risk is unaddressed."),
        get_spec("planning_analysis_decision"),
    )

    assert content == {}
    assert [(item.rule_id, item.severity) for item in diagnostics] == [("ANALYSIS004", "error")]


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
        " Criterion: verification is runnable. Expected observation: the command resolves. Proposed revision: name the runnable command in the step. Verdict: not met. Evidence: command output. Location: plan step.",
        "",
    )

    content, diagnostics = parse_and_validate(document, get_spec("planning_analysis_decision"))

    assert content == {}
    assert [(item.rule_id, item.severity) for item in diagnostics] == [
        ("ANALYSIS005", "error"),
        ("ANALYSIS005", "error"),
        ("ANALYSIS019", "error"),
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
type: planning_analysis_decision
status: request_changes
---
## Summary
- [SUM-1] Evidence is unavailable.

## What Came Up Short
- [PA-invalid] Plan-level: Criterion: the plan is runnable. Expected observation: it runs. Proposed revision: rename. Verdict: not met. Evidence: evidence. Location: plan. Cost: cost. Remaining work: rename.

## Criterion Verdicts
- [PA-invalid] Plan-level: Criterion: the plan is runnable. Expected observation: it runs. Proposed revision: rename. Verdict: not met. Evidence: evidence. Location: plan. Cost: cost.
"""

    content, diagnostics = parse_and_validate(document, get_spec("planning_analysis_decision"))

    assert content == {}
    assert [(item.rule_id, item.severity) for item in diagnostics] == [("ANALYSIS010", "error")]


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

    content, diagnostics = parse_and_validate(
        document, get_spec("policy_remediation_analysis_decision")
    )

    assert content == {}
    assert [item.rule_id for item in diagnostics] == ["ANALYSIS008", "ANALYSIS005"]


def test_not_evaluable_criterion_verdict_requires_failed_status() -> None:
    document = """---
type: planning_analysis_decision
status: request_changes
---
## Summary
- [SUM-1] Evidence is unavailable.

## What Came Up Short
- [PA-001] Plan-level: Criterion: the plan is runnable. Expected observation: the command resolves. Proposed revision: name the runnable command. Verdict: not met. Evidence: command unavailable. Location: plan. Cost: extra discovery work.

## Criterion Verdicts
- [PA-001] Plan-level: Criterion: the plan is runnable. Expected observation: the command resolves. Proposed revision: name the runnable command. Verdict: not evaluable. Evidence: command unavailable. Location: plan. Cost: extra discovery work.
"""

    content, diagnostics = parse_and_validate(document, get_spec("planning_analysis_decision"))

    assert content == {}
    assert [(item.rule_id, item.severity) for item in diagnostics] == [
        ("ANALYSIS007", "error"),
        ("ANALYSIS018", "error"),
    ]


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


def test_request_changes_allows_explicit_plan_level_target() -> None:
    content, diagnostics = parse_and_validate(
        _decision("Plan-level: The outcome omits an out-of-scope boundary."),
        get_spec("planning_analysis_decision"),
    )

    assert diagnostics == []
    assert content["finding_targets"] == {"PA-001": "plan-level"}


def test_request_changes_accepts_free_form_plan_reference() -> None:
    content, diagnostics = parse_and_validate(
        _decision("Plan reference: [install-flow] skips the retry path."),
        get_spec("planning_analysis_decision"),
    )

    assert diagnostics == []
    assert content["finding_ids"] == ["PA-001"]


def test_planning_findings_require_a_proposed_revision() -> None:
    document = _decision("The rollout risk is unaddressed.").replace(
        " Proposed revision: name the runnable command in the step.", ""
    )

    content, diagnostics = parse_and_validate(document, get_spec("planning_analysis_decision"))

    assert content == {}
    assert [item.rule_id for item in diagnostics] == [
        "ANALYSIS019",
        "ANALYSIS004",
        "ANALYSIS019",
    ]


def test_development_decision_is_free_form_body() -> None:
    document = """---
type: development_analysis_decision
status: request_changes
---
## Summary
- [SUM-1] One criterion is not met.

## What Came Up Short
- [DA-001] Criterion: behavior holds. Expected observation: focused evidence observes it. Verdict: not met. Evidence: output. Location: src/example.py:10. Remaining work: implement the missing behavior.

## Criterion Verdicts
- [DA-001] Criterion: behavior holds. Expected observation: focused evidence observes it. Verdict: not met. Evidence: output. Location: src/example.py:10.
"""

    content, diagnostics = parse_and_validate(document, get_spec("development_analysis_decision"))

    assert diagnostics == []
    assert content["status"] == "request_changes"
    assert content["type"] == "development_analysis_decision"


def test_development_decision_completed_is_accepted() -> None:
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
    assert content["status"] == "completed"


def test_development_decision_failed_is_accepted() -> None:
    document = """---
type: development_analysis_decision
status: failed
---
## Summary
- [SUM-1] Not evaluable.

## Criterion Verdicts
- [DA-001] Criterion: behavior holds. Verdict: not evaluable. Evidence: output. Location: src/example.py:10.
"""
    content, diagnostics = parse_and_validate(document, get_spec("development_analysis_decision"))
    assert diagnostics == []
    assert content["status"] == "failed"


def test_planning_verdict_cost_omission_regression() -> None:
    document = """---
type: planning_analysis_decision
status: request_changes
---
## Summary
- [SUM-1] The plan needs a cost.
## What Came Up Short
- [PA-001] Step: [S-2] lacks cost. Criterion: verification is runnable. Expected observation: the command resolves. Proposed revision: name the runnable command. Verdict: not met. Evidence: command output. Location: plan step.
## Criterion Verdicts
- [PA-001] Step: [S-2] Criterion: verification is runnable. Expected observation: the command resolves. Proposed revision: name the runnable command. Verdict: not met. Evidence: command output. Location: plan step.
"""

    content, diagnostics = parse_and_validate(document, get_spec("planning_analysis_decision"))

    assert content == {}
    analysis_005 = [d for d in diagnostics if d.rule_id == "ANALYSIS005"]
    assert len(analysis_005) == 1
    assert "Cost:" in analysis_005[0].message


def test_policy_remediation_verdict_does_not_require_cost() -> None:
    document = """---
type: policy_remediation_analysis_decision
status: completed
---
## Summary
- [SUM-1] No counterexample was found.

## Criterion Verdicts
- [PR-001] Criterion: the fact is current. Expected observation: the location matches. Verdict: met. Evidence: command output. Location: policy.md:10.
"""

    content, diagnostics = parse_and_validate(
        document, get_spec("policy_remediation_analysis_decision")
    )

    assert diagnostics == []
    assert content["criterion_verdict_ids"] == ["PR-001"]


def test_analysis011_message_aligns_with_inline_remedy_contract() -> None:
    document = """---
type: planning_analysis_decision
status: request_changes
---
## Summary
- [SUM-1] The plan needs correction.

## How To Fix
- [FIX-1] Add an inline Proposed revision: to each non-met verdict.

## What Came Up Short
- [PA-001] Plan-level: Criterion: parallel decomposition. Expected observation: split. Proposed revision: split. Verdict: not met. Evidence: cited paths. Location: plan prose. Cost: time.

## Criterion Verdicts
- [PA-001] Plan-level: Criterion: parallel decomposition. Expected observation: split. Proposed revision: split. Verdict: not met. Evidence: cited paths. Location: plan prose. Cost: time.
"""

    _content, diagnostics = parse_and_validate(document, get_spec("planning_analysis_decision"))

    analysis_011 = [d for d in diagnostics if d.rule_id == "ANALYSIS011"]
    assert len(analysis_011) == 1
    message = analysis_011[0].message
    assert "later phase" not in message.lower(), message
    assert "later pass" not in message.lower(), message
    assert "Proposed revision:" in message or "Remaining work:" in message, message
    assert "How To Fix" in message or "How To Fix" in message.lower(), message
