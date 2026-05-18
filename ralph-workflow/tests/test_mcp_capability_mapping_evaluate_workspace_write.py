"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

from ralph.mcp.protocol.capability_mapping import (
    evaluate_workspace_write,
)

# =============================================================================
# Helper function tests
# =============================================================================


class TestEvaluateWorkspaceWrite:
    def test_ephemeral_approved(self) -> None:
        result = evaluate_workspace_write({"status": "approved"}, {"status": "denied"})
        assert result.is_allowed() is True

    def test_tracked_approved(self) -> None:
        result = evaluate_workspace_write({"status": "denied"}, {"status": "approved"})
        assert result.is_allowed() is True

    def test_both_denied(self) -> None:
        result = evaluate_workspace_write({"status": "denied"}, {"status": "denied"})
        assert result.is_allowed() is False
