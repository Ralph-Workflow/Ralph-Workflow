"""Retry-hint corrective actions must follow the artifact validation failure."""

from __future__ import annotations

from ralph.mcp.artifacts.markdown import Diagnostic
from ralph.phases.required_artifacts import build_validation_retry_hint


def test_commit_message_failure_requires_rewriting_the_message() -> None:
    hint = build_validation_retry_hint(
        "commit_message",
        [Diagnostic(3, "Subject", "COMMIT001", "subject exceeds 72 characters")],
    )

    assert "rewrite the commit message" in hint.lower()
    assert "Fix the underlying document issue" not in hint


def test_development_result_invalid_status_frontmatter_keeps_free_form_guidance() -> None:
    """Free-form contract: a bad frontmatter status is repaired via document-edit wording."""
    hint = build_validation_retry_hint(
        "development_result",
        [Diagnostic(1, "Frontmatter", "DEV002", "status must be one of ['completed', 'partial', 'failed']")],
    )
    lowered = hint.lower()

    assert "free-form" in lowered
    assert "status" in lowered
    assert "fix the underlying document issue" not in hint.lower()


def test_development_result_structural_defect_keeps_document_repair_wording() -> None:
    hint = build_validation_retry_hint(
        "development_result",
        [Diagnostic(1, "Frontmatter", "DEV002", "type must be 'development_result'")],
    )

    assert "Fix the underlying document issue" in hint


def test_other_artifacts_keep_document_repair_wording() -> None:
    hint = build_validation_retry_hint(
        "product_spec",
        [Diagnostic(1, "Goals", "SPEC001", "missing required field")],
    )

    assert "Fix the underlying document issue" in hint
