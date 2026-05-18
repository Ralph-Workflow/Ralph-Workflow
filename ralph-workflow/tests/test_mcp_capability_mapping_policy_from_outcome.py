"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

from ralph.mcp.protocol.capability_mapping import (
    AccessDeniedCode,
    policy_from_outcome,
)

# =============================================================================
# Helper function tests
# =============================================================================


class TestPolicyFromOutcome:
    def test_approved_outcome(self) -> None:
        result = policy_from_outcome({"status": "approved"})
        assert result.is_allowed() is True

    def test_approved_with_restriction_outcome(self) -> None:
        result = policy_from_outcome({"status": "approved_with_restriction"})
        assert result.is_allowed() is True

    def test_denied_outcome(self) -> None:
        result = policy_from_outcome({"status": "denied"})
        assert result.is_allowed() is False
        assert result.code == AccessDeniedCode.CAPABILITY_DENIED
