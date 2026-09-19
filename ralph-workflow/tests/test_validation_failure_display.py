"""Validation recovery display helpers."""

from __future__ import annotations

from ralph.prompts.materialize import append_retry_footer


def test_validation_footer_is_operator_visible() -> None:
    rendered = append_retry_footer("VALIDATION FAILURE\nSPEC008", "VALIDATION FAILURE\nSPEC008")

    assert "VALIDATION FAILURE" in rendered
    assert rendered.rstrip().endswith("Do not resubmit unchanged work.")
