"""Pure behavior tests for simple markdown artifact specifications."""

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec, registered_specs
from ralph.mcp.artifacts.markdown.specs import (
    ANALYSIS_DECISION_SPECS,
    FIX_RESULT_SPEC,
    ISSUES_SPEC,
    PRODUCT_SPEC,
    SMOKE_TEST_RESULT_SPEC,
)


def _error_ids(text: str, artifact_type: str) -> set[str]:
    _, diagnostics = parse_and_validate(text, get_spec(artifact_type))
    return {diagnostic.rule_id for diagnostic in diagnostics if diagnostic.severity == "error"}


def test_specs_register_all_simple_artifact_types() -> None:
    expected = {
        "planning_analysis_decision",
        "development_analysis_decision",
        "review_analysis_decision",
        "issues",
        "fix_result",
        "smoke_test_result",
        "product_spec",
    }

    assert expected <= {spec.artifact_type for spec in registered_specs()}
    assert len(ANALYSIS_DECISION_SPECS) == 4
    assert get_spec("policy_remediation_analysis_decision") in ANALYSIS_DECISION_SPECS
    assert FIX_RESULT_SPEC.artifact_type == "fix_result"
    assert ISSUES_SPEC.artifact_type == "issues"
    assert PRODUCT_SPEC.artifact_type == "product_spec"
    assert SMOKE_TEST_RESULT_SPEC.artifact_type == "smoke_test_result"


def test_analysis_decision_coerces_status_and_requires_remediation() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: planning_analysis_decision
status: typo
---
## Summary
- [S1] The plan is ready
""",
        get_spec("planning_analysis_decision"),
    )

    assert content["status"] == "completed"
    assert [(diagnostic.rule_id, diagnostic.severity) for diagnostic in diagnostics] == [
        ("SPEC009", "warning")
    ]
    assert "SPEC010" in _error_ids(
        """---
type: planning_analysis_decision
status: request_changes
---
## Summary
- [S1] The plan needs revision
""",
        "planning_analysis_decision",
    )


def test_analysis_decision_keeps_stable_how_to_fix_identifier() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: review_analysis_decision
status: request_changes
---
## Summary
- [S1] Correct the regression
## What Came Up Short
- [W1] The error path lacks coverage
## How To Fix
- [F1] Add regression coverage
""",
        get_spec("review_analysis_decision"),
    )

    assert diagnostics == []
    assert content["how_to_fix"] == ["F1: Add regression coverage"]


def test_issues_coerces_vocabulary_but_rejects_malformed_issue_item() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: issues
status: issues_found
---
## Summary
- [S1] Found a defect
## Issues
- [I1] ralph/app.py | urgent | Missing validation
## What Came Up Short
- [W1] Input validation is absent
## How To Fix
- [F1] Add validation
""",
        ISSUES_SPEC,
    )

    assert content["issues"] == [
        {"path": "ralph/app.py", "severity": "low", "summary": "Missing validation"}
    ]
    assert [(diagnostic.rule_id, diagnostic.severity) for diagnostic in diagnostics] == [
        ("ISSUES003", "warning")
    ]
    assert "SPEC010" in _error_ids(
        """---
type: issues
status: issues_found
---
## Summary
- [S1] Found a defect
## Issues
- [I1] malformed
## What Came Up Short
- [W1] Input validation is absent
## How To Fix
- [F1] Add validation
""",
        "issues",
    )


def test_fix_result_requires_summary_and_changed_files() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: fix_result
---
## Summary
- [S1] Fixed validation
## Files Changed
- [F1] ralph/app.py
""",
        FIX_RESULT_SPEC,
    )

    assert diagnostics == []
    assert content["files_changed"] == "- ralph/app.py"
    assert "SPEC006" in _error_ids(
        """---
type: fix_result
---
## Summary
- [S1] Fixed validation
## Files Changed
""",
        "fix_result",
    )


def test_smoke_result_preserves_failed_and_headless_requirements() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: smoke_test_result
status: typo
output_file: tmp/smoke.log
---
## Summary
- [S1] Some smoke checks completed
## Headless Guide Checks
- [H1] Completion signal
""",
        SMOKE_TEST_RESULT_SPEC,
    )

    assert content["status"] == "partial"
    assert [(diagnostic.rule_id, diagnostic.severity) for diagnostic in diagnostics] == [
        ("SPEC009", "warning")
    ]
    assert "SPEC010" in _error_ids(
        """---
type: smoke_test_result
status: failed
output_file: tmp/smoke.log
---
## Summary
- [S1] The smoke test failed
## Headless Guide Checks
- [H1] Completion signal
""",
        "smoke_test_result",
    )


def test_product_spec_requires_the_existing_required_sections() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: product_spec
---
## Title
- [T1] Markdown artifacts
## Scope
- [S1] Move artifacts to markdown
## Goals
- [G1] Reduce authoring friction
## Users
- [U1] Agents
## Success Criteria
- [C1] Markdown validates
""",
        PRODUCT_SPEC,
    )

    assert diagnostics == []
    assert content["goals"] == ["Reduce authoring friction"]
    assert "SPEC008" in _error_ids(
        """---
type: product_spec
---
## Title
- [T1] Markdown artifacts
## Scope
- [S1] Move artifacts to markdown
## Goals
- [G1] Reduce authoring friction
## Users
- [U1] Agents
""",
        "product_spec",
    )
