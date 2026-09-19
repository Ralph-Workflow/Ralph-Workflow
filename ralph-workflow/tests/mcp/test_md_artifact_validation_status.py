"""Machine-readable validation state for markdown artifact tools."""

from __future__ import annotations

import json

from ralph.mcp.artifacts.markdown import Diagnostic
from ralph.mcp.tools.md_artifact import edit_result, validation_result
from tests._support.typed_accessors import must_mapping


def test_invalid_validation_result_reports_canonical_failure_envelope() -> None:
    result = validation_result(
        "product_spec",
        [Diagnostic(line=1, section=None, rule_id="SPEC008", message="missing section")],
    )

    assert result.is_error is True
    payload = must_mapping(json.loads(result.content[0].text))
    assert payload["status"] == "validation_failed"
    assert payload["severity"] == "error"
    assert isinstance(payload["message"], str)
    assert payload["diagnostics"]


def test_valid_validation_result_omits_failure_only_fields() -> None:
    result = validation_result("product_spec", [])

    assert result.is_error is False
    payload = must_mapping(json.loads(result.content[0].text))
    assert payload["valid"] is True
    assert "status" not in payload
    assert "severity" not in payload
    assert "message" not in payload


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
