"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

from ralph.mcp.tools.workspace import (
    is_policy_approved,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestIsPolicyApproved:
    def test_true_is_approved(self) -> None:
        assert is_policy_approved(True) is True

    def test_string_approved_is_approved(self) -> None:
        assert is_policy_approved("approved") is True
        assert is_policy_approved("allow") is True
        assert is_policy_approved("allowed") is True

    def test_string_approved_strips_whitespace(self) -> None:
        assert is_policy_approved("  approved  ") is True

    def test_other_strings_not_approved(self) -> None:
        assert is_policy_approved("denied") is False
        assert is_policy_approved("reject") is False

    def test_object_with_name_attribute(self) -> None:
        class Outcome:
            name = "approved"

        assert is_policy_approved(Outcome()) is True

    def test_object_with_value_attribute(self) -> None:
        class Outcome:
            value = "allow"

        assert is_policy_approved(Outcome()) is True

    def test_object_with_status_attribute(self) -> None:
        class Outcome:
            status = "allowed"

        assert is_policy_approved(Outcome()) is True

    def test_none_is_not_approved(self) -> None:
        assert is_policy_approved(None) is False
