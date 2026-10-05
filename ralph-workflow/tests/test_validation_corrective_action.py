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


def test_development_result_missing_evidence_requires_completing_the_work() -> None:
    hint = build_validation_retry_hint(
        "development_result",
        [Diagnostic(1, "Plan Items Proven", "DEV015", "missing=['S-2']")],
    )
    lowered = hint.lower()

    assert "complete the underlying work" in lowered
    assert "verif" in lowered
    assert "Fix the underlying document issue" not in hint


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
