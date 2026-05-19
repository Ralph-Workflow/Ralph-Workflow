"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

from ralph.mcp.protocol.capability_mapping import (
    extract_text_field,
)

# =============================================================================
# Helper function tests
# =============================================================================


class TestExtractTextField:
    def test_from_dict(self) -> None:
        assert extract_text_field({"name": "test"}, "name") == "test"

    def test_from_object(self) -> None:
        class Obj:
            name = "test"

        assert extract_text_field(Obj(), "name") == "test"

    def test_missing_from_dict(self) -> None:
        assert extract_text_field({}, "name") is None

    def test_missing_from_object(self) -> None:
        class Obj:
            pass

        assert extract_text_field(Obj(), "name") is None

    def test_non_string_returns_none(self) -> None:
        assert extract_text_field({"name": 123}, "name") is None
