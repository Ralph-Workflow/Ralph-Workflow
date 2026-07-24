"""Pure behavior tests for simple markdown artifact specifications."""

import pytest

from ralph.mcp.artifacts.markdown import MdArtifactSpec, parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec, registered_specs
from ralph.mcp.artifacts.markdown.specs import (
    ANALYSIS_DECISION_SPECS,
    FIX_RESULT_SPEC,
    ISSUES_SPEC,
    PRODUCT_SPEC,
    SMOKE_TEST_RESULT_SPEC,
)
from ralph.mcp.artifacts.typed_artifacts import (
    TypedArtifactValidationError,
    normalize_analysis_decision_content,
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


def test_analysis_decision_rejects_unknown_status_and_requires_remediation() -> None:
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

    assert content == {}
    assert any(
        diagnostic.rule_id == "SPEC010"
        and diagnostic.severity == "error"
        and "completed" in diagnostic.message
        and "request_changes" in diagnostic.message
        and "failed" in diagnostic.message
        for diagnostic in diagnostics
    )
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
- [W1] Add regression coverage
""",
        get_spec("review_analysis_decision"),
    )

    assert diagnostics == []
    assert content["how_to_fix"] == ["W1: Add regression coverage"]


def _analysis_spec_id(spec: MdArtifactSpec) -> str:
    return spec.artifact_type


def test_analysis_decision_model_rejects_completed_with_known_gaps() -> None:
    with pytest.raises(
        TypedArtifactValidationError,
        match="known gaps require a non-completed status",
    ):
        normalize_analysis_decision_content(
            {
                "status": "completed",
                "summary": "The analysis is complete despite a known gap",
                "what_came_up_short": ["A required behavior is missing"],
                "how_to_fix": ["GAP-1: Implement the missing behavior"],
            }
        )


@pytest.mark.parametrize("spec", ANALYSIS_DECISION_SPECS, ids=_analysis_spec_id)
def test_analysis_decision_rejects_completed_with_remediation_sections(
    spec: MdArtifactSpec,
) -> None:
    content, diagnostics = parse_and_validate(
        f"""---
type: {spec.artifact_type}
status: completed
---
## Summary
- [S1] The analysis claims completion
## What Came Up Short
- [GAP-1] A known gap remains
## How To Fix
- [GAP-1] Close the known gap
""",
        spec,
    )

    assert content == {}
    assert any(
        diagnostic.rule_id == "ANALYSIS002"
        and diagnostic.severity == "error"
        and "non-completed status" in diagnostic.message
        for diagnostic in diagnostics
    )


@pytest.mark.parametrize("spec", ANALYSIS_DECISION_SPECS, ids=_analysis_spec_id)
@pytest.mark.parametrize("status", ("request_changes", "failed"))
def test_analysis_decision_requires_exactly_matched_gap_and_fix_ids(
    spec: MdArtifactSpec,
    status: str,
) -> None:
    content, diagnostics = parse_and_validate(
        f"""---
type: {spec.artifact_type}
status: {status}
---
## Summary
- [S1] The analysis found multiple gaps
## What Came Up Short
- [GAP-1] This gap has a matching fix
- [GAP-2] This gap has no matching fix
## How To Fix
- [GAP-1] Close the matching gap
- [FIX-3] This fix has no matching gap
""",
        spec,
    )

    assert content == {}
    mismatch_errors = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.rule_id == "ANALYSIS003" and diagnostic.severity == "error"
    ]
    assert len(mismatch_errors) == 2
    assert any("GAP-2" in diagnostic.message for diagnostic in mismatch_errors)
    assert any("FIX-3" in diagnostic.message for diagnostic in mismatch_errors)


def test_issues_preserves_descriptive_severity_but_rejects_malformed_item() -> None:
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
        {"path": "ralph/app.py", "severity": "urgent", "summary": "Missing validation"}
    ]
    assert diagnostics == []
    assert "ISSUES002" in _error_ids(
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


def test_clean_issues_preserves_structured_review_evidence() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: issues
status: no_issues
---
## Summary
- [S1] The implementation satisfies the reviewed requirements.
## Review Evidence
- [E1] Plan compliance | Every acceptance criterion maps to an implementation check.
- [E2] Security | No security-relevant code changed.
""",
        ISSUES_SPEC,
    )

    assert diagnostics == []
    assert content["review_evidence"] == [
        "Plan compliance | Every acceptance criterion maps to an implementation check.",
        "Security | No security-relevant code changed.",
    ]


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


def test_smoke_result_rejects_unknown_status_and_preserves_failed_requirements() -> None:
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

    assert content == {}
    assert any(
        diagnostic.rule_id == "SPEC010"
        and diagnostic.severity == "error"
        and "passed" in diagnostic.message
        and "failed" in diagnostic.message
        and "partial" in diagnostic.message
        for diagnostic in diagnostics
    )
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


@pytest.mark.parametrize(
    ("artifact_type", "frontmatter", "body"),
    (
        pytest.param(
            "development_result",
            ("type: development_result", "status: partial"),
            "## Summary\n- [S1] Work is partially complete\n"
            "## Files Changed\n- [F1] src/example.py\n"
            "## Next Steps\n- [N1] Finish the remaining work\n"
            "## Continuation\n- [C1] session-123",
            id="development-result",
        ),
        pytest.param(
            "smoke_test_result",
            (
                "type: smoke_test_result",
                "status: passed",
                "output_file: tmp/smoke.log",
            ),
            "## Summary\n- [S1] Smoke run passed\n"
            "## Headless Guide Checks\n- [H1] Completion signal checked",
            id="smoke-test-result",
        ),
        pytest.param(
            "commit_message",
            ("type: commit", "subject: fix(parser): accept descriptive extensions"),
            "## Body\n- [B1] Preserve consumed fields.",
            id="commit-message",
        ),
        pytest.param(
            "issues",
            ("type: issues", "status: no_issues"),
            "## Summary\n- [S1] No review issues",
            id="issues",
        ),
        pytest.param(
            "fix_result",
            ("type: fix_result",),
            "## Summary\n- [S1] Fix completed\n## Files Changed\n- [F1] src/example.py",
            id="fix-result",
        ),
        pytest.param(
            "product_spec",
            ("type: product_spec",),
            "## Title\n- [T1] Example product\n"
            "## Scope\n- [S1] Example scope\n"
            "## Goals\n- [G1] Example goal\n"
            "## Users\n- [U1] Example user\n"
            "## Success Criteria\n- [C1] Example criterion",
            id="product-spec",
        ),
        pytest.param(
            "commit_cleanup",
            ("type: commit_cleanup", "analysis_complete: false"),
            "## Actions\n- [A1] add_to_gitignore | *.tmp",
            id="commit-cleanup",
        ),
        *(
            pytest.param(
                spec.artifact_type,
                (f"type: {spec.artifact_type}", "status: completed"),
                "## Summary\n- [S1] Analysis finished",
                id=spec.artifact_type,
            )
            for spec in ANALYSIS_DECISION_SPECS
        ),
    ),
)
def test_non_plan_specs_ignore_unknown_unconsumed_extensions(
    artifact_type: str,
    frontmatter: tuple[str, ...],
    body: str,
) -> None:
    text = "\n".join(
        (
            "---",
            *frontmatter,
            "agent_note: ignored by canonical mapping",
            "---",
            body,
            "## Agent Notes",
            "Free-form implementation context ignored by canonical mapping.",
            "",
        )
    )

    content, diagnostics = parse_and_validate(text, get_spec(artifact_type))

    assert diagnostics == []
    assert content
