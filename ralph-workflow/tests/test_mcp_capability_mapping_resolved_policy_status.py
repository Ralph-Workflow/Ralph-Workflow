"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

from ralph.mcp.protocol.capability_mapping import (
    PolicyOutcomeStatus,
    resolved_policy_status,
)

# =============================================================================
# Helper function tests
# =============================================================================


class TestResolvedPolicyStatus:
    def test_approved_values(self) -> None:
        assert resolved_policy_status({}, "approved", None) == PolicyOutcomeStatus.APPROVED
        assert resolved_policy_status({}, "allow", None) == PolicyOutcomeStatus.APPROVED
        assert resolved_policy_status({}, "allowed", None) == PolicyOutcomeStatus.APPROVED

    def test_approved_with_restriction_values(self) -> None:
        assert (
            resolved_policy_status({}, "approved_with_restriction", None)
            == PolicyOutcomeStatus.APPROVED_WITH_RESTRICTION
        )

    def test_denied_values(self) -> None:
        assert resolved_policy_status({}, "denied", None) == PolicyOutcomeStatus.DENIED
        assert resolved_policy_status({}, "deny", None) == PolicyOutcomeStatus.DENIED

    def test_dict_with_reason_denies(self) -> None:
        # Any dict with "reason" key is denied
        assert resolved_policy_status({"reason": "because"}, "", None) == PolicyOutcomeStatus.DENIED

    def test_reason_present_denies(self) -> None:
        assert resolved_policy_status({}, "", "because") == PolicyOutcomeStatus.DENIED

    def test_unknown_returns_none(self) -> None:
        assert resolved_policy_status({}, "unknown_status", None) is None
