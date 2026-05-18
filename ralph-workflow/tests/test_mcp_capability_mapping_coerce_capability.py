"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import (
    Capability,
    coerce_capability,
)

# =============================================================================
# Helper function tests
# =============================================================================


class TestCoerceCapability:
    def test_capability_passthrough(self) -> None:
        assert coerce_capability(Capability.WORKSPACE_READ) == Capability.WORKSPACE_READ

    def test_string_lookup(self) -> None:
        assert coerce_capability("workspace.read") == Capability.WORKSPACE_READ

    def test_alias_lookup(self) -> None:
        assert coerce_capability("process_exec_bounded") == Capability.PROCESS_EXEC_BOUNDED

    def test_web_search_alias_lookup(self) -> None:
        assert coerce_capability("web_search") == Capability.WEB_SEARCH

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            coerce_capability("unknown_capability")
