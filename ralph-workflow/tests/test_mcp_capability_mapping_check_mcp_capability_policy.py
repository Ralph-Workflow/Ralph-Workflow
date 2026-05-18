"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

from ralph.mcp.protocol.capability_mapping import (
    Capability,
    check_mcp_capability_policy,
)

# =============================================================================
# Helper function tests
# =============================================================================


class TestCheckMcpCapabilityPolicy:
    def test_workspace_write_any_delegates_to_evaluate_workspace_write(self) -> None:
        result = check_mcp_capability_policy(
            "WorkspaceWriteAny",
            {"status": "approved"},
            {"status": "denied"},
            None,
        )
        assert result.is_allowed() is True

    def test_file_write_delegates_to_evaluate_workspace_write(self) -> None:
        result = check_mcp_capability_policy(
            "FileWrite",
            {"status": "approved"},
            {"status": "denied"},
            None,
        )
        assert result.is_allowed() is True

    def test_workspace_coordination_always_allowed(self) -> None:
        result = check_mcp_capability_policy(
            "WorkspaceCoordination",
            {"status": "denied"},
            {"status": "denied"},
            None,
        )
        assert result.is_allowed() is True

    def test_workspace_read_with_mapped_outcome(self) -> None:
        result = check_mcp_capability_policy(
            "WorkspaceRead",
            {"status": "denied"},
            {"status": "denied"},
            (Capability.WORKSPACE_READ, {"status": "approved"}),
        )
        assert result.is_allowed() is True

    def test_unknown_capability_denied(self) -> None:
        result = check_mcp_capability_policy(
            "UnknownCapability",
            {"status": "denied"},
            {"status": "denied"},
            None,
        )
        assert result.is_allowed() is False

    def test_web_search_delegates_to_mapped_capability(self) -> None:
        result = check_mcp_capability_policy(
            "WebSearch",
            {"status": "denied"},
            {"status": "denied"},
            (Capability.WEB_SEARCH, {"status": "approved"}),
        )
        assert result.is_allowed() is True
