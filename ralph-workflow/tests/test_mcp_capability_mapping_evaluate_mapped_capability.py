"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

from ralph.mcp.protocol.capability_mapping import (
    Capability,
    evaluate_mapped_capability,
)

# =============================================================================
# Helper function tests
# =============================================================================


class TestEvaluateMappedCapability:
    def test_allowed_capability(self) -> None:
        result = evaluate_mapped_capability(
            "WorkspaceRead",
            (Capability.WORKSPACE_READ, {"status": "approved"}),
        )
        assert result.is_allowed() is True

    def test_denied_capability(self) -> None:
        result = evaluate_mapped_capability(
            "WorkspaceRead",
            (Capability.WORKSPACE_READ, {"status": "denied"}),
        )
        assert result.is_allowed() is False

    def test_unknown_capability_string(self) -> None:
        result = evaluate_mapped_capability("UnknownCap", None)
        assert result.is_allowed() is False

    def test_none_mapped_outcome(self) -> None:
        result = evaluate_mapped_capability("WorkspaceRead", None)
        assert result.is_allowed() is False

    def test_web_search_allowed_capability(self) -> None:
        result = evaluate_mapped_capability(
            "WebSearch",
            (Capability.WEB_SEARCH, {"status": "approved"}),
        )
        assert result.is_allowed() is True
