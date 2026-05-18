"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

from ralph.mcp.protocol.capability_mapping import (
    Capability,
    lookup_ralph_capability,
)

# =============================================================================
# Helper function tests
# =============================================================================


class TestLookupRalphCapability:
    def test_workspace_read(self) -> None:
        result = lookup_ralph_capability("WorkspaceRead")
        assert result == Capability.WORKSPACE_READ

    def test_git_status_read(self) -> None:
        result = lookup_ralph_capability("GitStatusRead")
        assert result == Capability.GIT_STATUS_READ

    def test_web_search(self) -> None:
        result = lookup_ralph_capability("WebSearch")
        assert result == Capability.WEB_SEARCH

    def test_unknown_returns_none(self) -> None:
        result = lookup_ralph_capability("UnknownCapability")
        assert result is None
