"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

from ralph.mcp.protocol.capability_mapping import (
    AccessDecision,
    AccessDeniedCode,
)

# =============================================================================
# Helper function tests
# =============================================================================


class TestAccessDecision:
    def test_allow_class_method(self) -> None:
        decision = AccessDecision.allow()
        assert decision.allowed is True
        assert decision.reason is None

    def test_deny_class_method(self) -> None:
        decision = AccessDecision.deny("test reason", AccessDeniedCode.CAPABILITY_DENIED)
        assert decision.allowed is False
        assert decision.reason == "test reason"
        assert decision.code == AccessDeniedCode.CAPABILITY_DENIED

    def test_is_allowed(self) -> None:
        assert AccessDecision.allow().is_allowed() is True
        assert AccessDecision.deny("x", AccessDeniedCode.CAPABILITY_DENIED).is_allowed() is False
