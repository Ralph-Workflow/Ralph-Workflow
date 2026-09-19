"""Regression coverage for retry footer rendering."""

from __future__ import annotations

from ralph.prompts.materialize import append_retry_footer


def test_validation_retry_footer_is_the_final_prompt_block() -> None:
    rendered = append_retry_footer(
        "VALIDATION FAILURE\nSPEC008 missing proof\n\nLong prompt body.",
        "VALIDATION FAILURE\nSPEC008 missing proof",
    )

    assert rendered.startswith("VALIDATION FAILURE")
    assert rendered.rstrip().endswith("Do not resubmit unchanged work.")


def test_non_validation_retry_hint_does_not_add_validation_footer() -> None:
    rendered = append_retry_footer("Prompt body.", "PIPELINE INPUT MISSING")

    assert rendered == "Prompt body."
