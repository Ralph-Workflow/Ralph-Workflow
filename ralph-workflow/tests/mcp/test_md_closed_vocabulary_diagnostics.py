"""Black-box regressions for consumed frontmatter vocabulary diagnostics."""

import pytest

from ralph.mcp.artifacts.markdown import MdArtifactSpec, parse_and_validate
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


def _analysis_spec_id(spec: MdArtifactSpec) -> str:
    return spec.artifact_type


@pytest.mark.parametrize("spec", ANALYSIS_DECISION_SPECS, ids=_analysis_spec_id)
@pytest.mark.parametrize("status_line", (None, "status:"))
def test_analysis_status_missing_or_empty_names_every_accepted_value(
    spec: MdArtifactSpec,
    status_line: str | None,
) -> None:
    artifact_type = spec.artifact_type
    frontmatter = [f"type: {artifact_type}"]
    if status_line is not None:
        frontmatter.append(status_line)
    _assert_closed_vocabulary_error(
        artifact_type,
        _document(tuple(frontmatter), "## Summary\n- [S1] Analysis finished"),
        field_line=1,
        valid_vocabulary=("completed", "request_changes", "failed"),
    )


@pytest.mark.parametrize("spec", ANALYSIS_DECISION_SPECS, ids=_analysis_spec_id)
def test_analysis_duplicate_status_diagnostic_names_every_accepted_value(
    spec: MdArtifactSpec,
) -> None:
    content, diagnostics = parse_and_validate(
        _document(
            (
                f"type: {spec.artifact_type}",
                "status: completed",
                "status: done",
            ),
            "## Summary\n- [S1] Analysis finished",
        ),
        spec,
    )

    assert content == {}
    duplicate_errors = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.rule_id == "MD006" and diagnostic.severity == "error"
    ]
    assert len(duplicate_errors) == 1
    assert duplicate_errors[0].line == 4
    assert all(
        value in duplicate_errors[0].message for value in ("completed", "request_changes", "failed")
    )


@pytest.mark.parametrize(
    ("artifact_type", "frontmatter", "body", "valid_vocabulary"),
    (
        pytest.param(
            "development_result",
            ("type: development_result",),
            "## Summary\n- [S1] Work finished\n## Files Changed\n- [F1] src/example.py",
            ("completed", "partial"),
            id="development-result",
        ),
        pytest.param(
            "smoke_test_result",
            ("type: smoke_test_result", "output_file: tmp/smoke.log"),
            "## Summary\n- [S1] Smoke run finished\n"
            "## Headless Guide Checks\n- [H1] Completion signal checked",
            ("passed", "failed", "partial"),
            id="smoke-test-result",
        ),
        pytest.param(
            "issues",
            ("type: issues",),
            "## Summary\n- [S1] No review issues",
            ("issues_found", "no_issues"),
            id="issues",
        ),
    ),
)
@pytest.mark.parametrize("empty_status", (False, True), ids=("missing", "empty"))
def test_consumed_status_missing_or_empty_names_every_accepted_value(
    artifact_type: str,
    frontmatter: tuple[str, ...],
    body: str,
    valid_vocabulary: tuple[str, ...],
    *,
    empty_status: bool,
) -> None:
    fields = (*frontmatter, "status:") if empty_status else frontmatter
    _assert_closed_vocabulary_error(
        artifact_type,
        _document(fields, body),
        field_line=1,
        valid_vocabulary=valid_vocabulary,
    )


@pytest.mark.parametrize(
    ("artifact_type", "frontmatter", "valid_vocabulary", "requires_sections"),
    (
        # development_result only requires sections for ``status: completed``;
        # an unknown status leaves the body free-form, so the vocabulary error
        # is the only one raised.
        pytest.param(
            "development_result",
            ("type: development_result", "status: done"),
            ("completed", "partial"),
            False,
            id="development-result",
        ),
        pytest.param(
            "smoke_test_result",
            ("type: smoke_test_result", "status: done", "output_file: tmp/smoke.log"),
            ("passed", "failed", "partial"),
            True,
            id="smoke-test-result",
        ),
        pytest.param(
            "issues",
            ("type: issues", "status: done"),
            ("issues_found", "no_issues"),
            True,
            id="issues",
        ),
        *(
            pytest.param(
                spec.artifact_type,
                (f"type: {spec.artifact_type}", "status: done"),
                ("completed", "request_changes", "failed"),
                True,
                id=spec.artifact_type,
            )
            for spec in ANALYSIS_DECISION_SPECS
        ),
    ),
)
def test_consumed_status_diagnostic_survives_missing_required_sections(
    artifact_type: str,
    frontmatter: tuple[str, ...],
    valid_vocabulary: tuple[str, ...],
    requires_sections: bool,
) -> None:
    content, diagnostics = parse_and_validate(
        _document(frontmatter, ""),
        get_spec(artifact_type),
    )

    assert content == {}
    assert requires_sections == any(
        diagnostic.rule_id == "SPEC008" and diagnostic.severity == "error"
        for diagnostic in diagnostics
    )
    status_errors = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.severity == "error"
        and all(value in diagnostic.message for value in valid_vocabulary)
    ]
    assert len(status_errors) == 1
    assert status_errors[0].line == 3


@pytest.mark.parametrize(
    "analysis_complete_line", (None, "analysis_complete:", "analysis_complete: done")
)
def test_commit_cleanup_analysis_complete_teaches_its_closed_vocabulary(
    analysis_complete_line: str | None,
) -> None:
    frontmatter = ["type: commit_cleanup"]
    if analysis_complete_line is not None:
        frontmatter.append(analysis_complete_line)
    expected_line = 3 if analysis_complete_line == "analysis_complete: done" else 1
    _assert_closed_vocabulary_error(
        "commit_cleanup",
        _document(tuple(frontmatter), "## Actions\n- [A1] add_to_gitignore | *.tmp"),
        field_line=expected_line,
        valid_vocabulary=("true", "false"),
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
