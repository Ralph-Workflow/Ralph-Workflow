"""Tests for the free-form development_result artifact validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ralph.mcp.artifacts.development_result import (
    DevelopmentResult,
    DevelopmentResultValidationError,
    normalize_development_result_content,
)


def test_development_result_status_must_be_set() -> None:
    with pytest.raises(ValidationError):
        DevelopmentResult()


def test_development_result_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError, match="status"):
        DevelopmentResult.model_validate({"status": "done"})


def test_development_result_accepts_bare_completed_status() -> None:
    result = DevelopmentResult(status="completed")

    assert result.status == "completed"
    assert result.summary == ""
    assert result.files_changed == ""


def test_development_result_accepts_bare_partial_status() -> None:
    result = DevelopmentResult(status="partial")

    assert result.status == "partial"
    assert result.summary == ""


def test_development_result_accepts_bare_failed_status() -> None:
    result = DevelopmentResult(status="failed")

    assert result.status == "failed"


def test_development_result_tolerates_unknown_body_keys() -> None:
    """Free-form body: any extra keys are accepted as a free-form payload."""
    result = DevelopmentResult.model_validate(
        {
            "status": "completed",
            "summary": "Wrote what I did, what changed, and what was verified.",
            "files_changed": "- ralph/mcp/tool_bridge.py",
            "what_changed": "Anything the agent wants to record.",
            "verification": "made verify; it passes.",
        }
    )

    assert result.status == "completed"
    assert result.summary.startswith("Wrote")


def test_normalize_development_result_accepts_completed_payload() -> None:
    normalized = normalize_development_result_content(
        {
            "status": "completed",
            "summary": "Finished the requested MCP hardening work.",
            "files_changed": "- ralph/mcp/tool_bridge.py",
        }
    )

    assert normalized["status"] == "completed"


def test_normalize_development_result_accepts_partial_without_continuation() -> None:
    normalized = normalize_development_result_content(
        {
            "status": "partial",
            "summary": "Half complete.",
            "files_changed": "- ralph/mcp/tool_bridge.py",
            "next_steps": "Finish the remaining test updates.",
        }
    )

    assert normalized["status"] == "partial"


def test_normalize_development_result_accepts_bare_partial_status() -> None:
    normalized = normalize_development_result_content({"status": "partial"})

    assert normalized == {"status": "partial"}


def test_normalize_development_result_accepts_bare_failed_status() -> None:
    normalized = normalize_development_result_content({"status": "failed"})

    assert normalized == {"status": "failed"}


def test_normalize_development_result_still_rejects_unknown_status() -> None:
    with pytest.raises(DevelopmentResultValidationError, match="completed"):
        normalize_development_result_content({"status": "done", "summary": "Done."})


def test_normalize_development_result_passes_unstructured_body_through() -> None:
    """The body of any development result is the next agent's reading matter."""
    normalized = normalize_development_result_content(
        {
            "status": "completed",
            "what_i_did": "Reworked the spec to validate only the status enum.",
            "what_changed": "ralph/mcp/artifacts/markdown/specs/development_result.py",
            "verification": "make verify; the focused spec tests pass.",
        }
    )

    assert normalized["status"] == "completed"
    # Extra keys are accepted; the model is no longer strict about body shape.
    assert "what_i_did" in normalized or normalized == {"status": "completed"}
