"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

from ralph.mcp.protocol.capability_mapping import (
    normalize_token,
)

# =============================================================================
# Helper function tests
# =============================================================================


class TestNormalizeToken:
    def test_lowercases(self) -> None:
        assert normalize_token("Hello") == "hello"

    def test_replaces_dashes(self) -> None:
        assert normalize_token("hello-world") == "hello_world"

    def test_replaces_spaces(self) -> None:
        assert normalize_token("hello world") == "hello_world"

    def test_strips_whitespace(self) -> None:
        assert normalize_token("  hello  ") == "hello"
