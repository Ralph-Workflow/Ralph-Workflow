"""Tests for structured development_result artifact validation."""

from __future__ import annotations

import pytest

from ralph.mcp.artifacts.development_result import (
    DevelopmentResultValidationError,
    normalize_development_result_content,
)


def test_normalize_development_result_accepts_completed_payload() -> None:
    normalized = normalize_development_result_content(
        {
            "status": "completed",
            "summary": "Finished the requested MCP hardening work.",
            "files_changed": "- ralph/mcp/tool_bridge.py",
        }
    )

    assert normalized["status"] == "completed"


def test_normalize_development_result_rejects_partial_without_continuation() -> None:
    with pytest.raises(DevelopmentResultValidationError, match="continuation"):
        normalize_development_result_content(
            {
                "status": "partial",
                "summary": "Half complete.",
                "files_changed": "- ralph/mcp/tool_bridge.py",
                "next_steps": "Finish the remaining test updates.",
            }
        )
