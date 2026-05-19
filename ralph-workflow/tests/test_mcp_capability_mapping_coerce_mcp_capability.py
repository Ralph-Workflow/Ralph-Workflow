"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import (
    McpCapability,
    coerce_mcp_capability,
)

# =============================================================================
# Helper function tests
# =============================================================================


class TestCoerceMcpCapability:
    def test_mcp_capability_passthrough(self) -> None:
        assert coerce_mcp_capability(McpCapability.WORKSPACE_READ) == McpCapability.WORKSPACE_READ

    def test_string_lookup(self) -> None:
        assert coerce_mcp_capability("WorkspaceRead") == McpCapability.WORKSPACE_READ

    def test_alias_with_dots(self) -> None:
        assert coerce_mcp_capability("workspace.read") == McpCapability.WORKSPACE_READ

    def test_web_search_lookup(self) -> None:
        assert coerce_mcp_capability("WebSearch") == McpCapability.WEB_SEARCH

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            coerce_mcp_capability("UnknownCapability")
