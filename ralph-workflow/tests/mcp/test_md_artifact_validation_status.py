"""Machine-readable validation state for markdown artifact tools."""

from __future__ import annotations

from ralph.mcp.tools.md_artifact import edit_result, validation_result


def test_invalid_validation_result_reports_error_status() -> None:
    result = validation_result("product_spec", [])

    assert result.is_error is False


def test_invalid_edit_result_reports_validation_failure_status() -> None:
    result = edit_result(
        "product_spec",
        "---\ntype: product_spec\n---\n",
        "",
        1,
        "applied",
        ambiguous_edits=[],
        submitted=False,
    )

    assert result.is_error is True
    assert result.content[0].text is not None
    assert "validation_failed" in result.content[0].text
