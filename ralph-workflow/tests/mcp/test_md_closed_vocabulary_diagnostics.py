"""Black-box regressions for consumed frontmatter vocabulary diagnostics."""

import pytest

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.markdown.specs import ANALYSIS_DECISION_SPECS


def _document(frontmatter: tuple[str, ...], body: str) -> str:
    return "\n".join(("---", *frontmatter, "---", body, ""))


def _assert_closed_vocabulary_error(
    artifact_type: str,
    markdown: str,
    *,
    field_line: int,
    valid_vocabulary: tuple[str, ...],
) -> None:
    content, diagnostics = parse_and_validate(markdown, get_spec(artifact_type))

    assert content == {}
    matching_errors = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.severity == "error"
        and all(value in diagnostic.message for value in valid_vocabulary)
    ]
    assert len(matching_errors) == 1
    assert matching_errors[0].line == field_line


@pytest.mark.parametrize(
    ("artifact_type", "frontmatter", "body", "valid_vocabulary"),
    (
        pytest.param(
            "development_result",
            ("type: development_result", "status: done"),
            "## Summary\n- [S1] Work finished\n## Files Changed\n- [F1] src/example.py",
            ("completed", "partial"),
            id="development-result",
        ),
        pytest.param(
            "smoke_test_result",
            ("type: smoke_test_result", "status: done", "output_file: tmp/smoke.log"),
            "## Summary\n- [S1] Smoke run finished\n"
            "## Headless Guide Checks\n- [H1] Completion signal checked",
            ("passed", "failed", "partial"),
            id="smoke-test-result",
        ),
        pytest.param(
            "issues",
            ("type: issues", "status: done"),
            "## Summary\n- [S1] No review issues",
            ("issues_found", "no_issues"),
            id="issues",
        ),
        *(
            pytest.param(
                spec.artifact_type,
                (f"type: {spec.artifact_type}", "status: done"),
                "## Summary\n- [S1] Analysis finished",
                ("completed", "request_changes", "failed"),
                id=spec.artifact_type,
            )
            for spec in ANALYSIS_DECISION_SPECS
        ),
    ),
)
def test_closed_status_regression_done_hard_fails_at_frontmatter_line(
    artifact_type: str,
    frontmatter: tuple[str, ...],
    body: str,
    valid_vocabulary: tuple[str, ...],
) -> None:
    """Regress the user-requested closed-status diagnostic anchoring contract."""
    _assert_closed_vocabulary_error(
        artifact_type,
        _document(frontmatter, body),
        field_line=3,
        valid_vocabulary=valid_vocabulary,
    )


@pytest.mark.parametrize(
    ("artifact_type", "frontmatter", "body", "valid_vocabulary"),
    (
        pytest.param(
            "development_result",
            ("type: wrong", "status: completed"),
            "## Summary\n- [S1] Work finished\n## Files Changed\n- [F1] src/example.py",
            ("development_result",),
            id="development-result",
        ),
        pytest.param(
            "smoke_test_result",
            ("type: wrong", "status: passed", "output_file: tmp/smoke.log"),
            "## Summary\n- [S1] Smoke run passed\n"
            "## Headless Guide Checks\n- [H1] Completion signal checked",
            ("smoke_test_result",),
            id="smoke-test-result",
        ),
        pytest.param(
            "commit_message",
            ("type: wrong",),
            "",
            ("commit", "skip"),
            id="commit-message",
        ),
        pytest.param(
            "issues",
            ("type: wrong", "status: no_issues"),
            "## Summary\n- [S1] No review issues",
            ("issues",),
            id="issues",
        ),
        pytest.param(
            "fix_result",
            ("type: wrong",),
            "## Summary\n- [S1] Fix completed\n## Files Changed\n- [F1] src/example.py",
            ("fix_result",),
            id="fix-result",
        ),
        pytest.param(
            "product_spec",
            ("type: wrong",),
            "## Title\n- [T1] Example product\n"
            "## Scope\n- [S1] Example scope\n"
            "## Goals\n- [G1] Example goal\n"
            "## Users\n- [U1] Example user\n"
            "## Success Criteria\n- [C1] Example criterion",
            ("product_spec",),
            id="product-spec",
        ),
        pytest.param(
            "commit_cleanup",
            ("type: wrong", "analysis_complete: false"),
            "## Actions\n- [A1] add_to_gitignore | *.tmp",
            ("commit_cleanup",),
            id="commit-cleanup",
        ),
        *(
            pytest.param(
                spec.artifact_type,
                ("type: wrong", "status: completed"),
                "## Summary\n- [S1] Analysis finished",
                (spec.artifact_type,),
                id=spec.artifact_type,
            )
            for spec in ANALYSIS_DECISION_SPECS
        ),
    ),
)
def test_closed_type_regression_wrong_hard_fails_at_frontmatter_line(
    artifact_type: str,
    frontmatter: tuple[str, ...],
    body: str,
    valid_vocabulary: tuple[str, ...],
) -> None:
    """Regress the user-requested closed-type diagnostic anchoring contract."""
    _assert_closed_vocabulary_error(
        artifact_type,
        _document(frontmatter, body),
        field_line=2,
        valid_vocabulary=valid_vocabulary,
    )
