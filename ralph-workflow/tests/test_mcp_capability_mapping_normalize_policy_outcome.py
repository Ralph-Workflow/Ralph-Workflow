"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import (
    PolicyOutcome,
    PolicyOutcomeStatus,
    normalize_policy_outcome,
)

# =============================================================================
# Helper function tests
# =============================================================================


class TestNormalizePolicyOutcome:
    def test_true_is_approved(self) -> None:
        result = normalize_policy_outcome(True)
        assert result.status == PolicyOutcomeStatus.APPROVED

    def test_policy_outcome_passthrough(self) -> None:
        outcome = PolicyOutcome(status=PolicyOutcomeStatus.APPROVED)
        assert normalize_policy_outcome(outcome) == outcome

    def test_dict_approved(self) -> None:
        result = normalize_policy_outcome({"status": "approved"})
        assert result.status == PolicyOutcomeStatus.APPROVED

    def test_dict_with_reason(self) -> None:
        result = normalize_policy_outcome({"status": "approved", "reason": "test reason"})
        assert result.status == PolicyOutcomeStatus.APPROVED
        assert result.reason == "test reason"

    def test_unsupported_raises(self) -> None:
        with pytest.raises(ValueError):
            normalize_policy_outcome("unsupported_value")
