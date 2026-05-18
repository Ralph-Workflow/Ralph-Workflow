"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

from ralph.mcp.protocol.capability_mapping import (
    extract_named_value,
)

# =============================================================================
# Helper function tests
# =============================================================================


class TestExtractNamedValue:
    def test_string_passthrough(self) -> None:
        assert extract_named_value("test") == "test"

    def test_enum_value(self) -> None:
        class MockEnum:
            value = "test_value"

        assert extract_named_value(MockEnum()) == "test_value"

    def test_status_field(self) -> None:
        class Obj:
            status = "approved"

        assert extract_named_value(Obj()) == "approved"

    def test_name_field(self) -> None:
        class Obj:
            name = "my_name"

        assert extract_named_value(Obj()) == "my_name"

    def test_value_field(self) -> None:
        class Obj:
            value = "my_value"

        assert extract_named_value(Obj()) == "my_value"
