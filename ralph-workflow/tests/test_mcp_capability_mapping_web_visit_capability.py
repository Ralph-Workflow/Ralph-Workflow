"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import (
    MCP_TO_RALPH_CAPABILITY_MAP,
    Capability,
    McpCapability,
    SessionDrain,
    check_mcp_capability_policy,
    coerce_capability,
    coerce_mcp_capability,
    evaluate_mapped_capability,
    lookup_ralph_capability,
)
from ralph.prompts.template_variables import CapabilitySet

_COMMIT_DRAINS: frozenset[SessionDrain] = frozenset(
    {SessionDrain.DEVELOPMENT_COMMIT, SessionDrain.REVIEW_COMMIT, SessionDrain.COMMIT}
)


# =============================================================================
# Helper function tests
# =============================================================================


class TestWebVisitCapability:
    """Tests for WebVisit capability mapping."""

    def test_capability_web_visit_exists(self) -> None:
        assert hasattr(Capability, "WEB_VISIT")
        assert Capability.WEB_VISIT == "web.visit"

    def test_mcp_capability_web_visit_exists(self) -> None:
        assert hasattr(McpCapability, "WEB_VISIT")
        assert McpCapability.WEB_VISIT == "WebVisit"

    def test_web_visit_alias_dot_notation_in_coerce_capability(self) -> None:
        result = coerce_capability("web.visit")
        assert result == Capability.WEB_VISIT

    def test_web_visit_alias_underscore_notation_in_coerce_capability(self) -> None:
        result = coerce_capability("web_visit")
        assert result == Capability.WEB_VISIT

    def test_web_visit_alias_dot_notation_in_coerce_mcp_capability(self) -> None:
        result = coerce_mcp_capability("web.visit")
        assert result == McpCapability.WEB_VISIT

    def test_web_visit_alias_underscore_notation_in_coerce_mcp_capability(self) -> None:
        result = coerce_mcp_capability("web_visit")
        assert result == McpCapability.WEB_VISIT

    def test_web_visit_alias_capitalized_in_coerce_mcp_capability(self) -> None:
        result = coerce_mcp_capability("WebVisit")
        assert result == McpCapability.WEB_VISIT

    def test_web_visit_maps_to_ralph_capability(self) -> None:
        result = lookup_ralph_capability("WebVisit")
        assert result == Capability.WEB_VISIT

    def test_web_visit_in_mcp_to_ralph_map(self) -> None:
        assert MCP_TO_RALPH_CAPABILITY_MAP[McpCapability.WEB_VISIT] == Capability.WEB_VISIT

    def test_web_visit_policy_allowed(self) -> None:
        result = check_mcp_capability_policy(
            "WebVisit",
            {"status": "denied"},
            {"status": "denied"},
            (Capability.WEB_VISIT, {"status": "approved"}),
        )
        assert result.is_allowed() is True

    def test_web_visit_policy_denied(self) -> None:
        result = check_mcp_capability_policy(
            "WebVisit",
            {"status": "denied"},
            {"status": "denied"},
            (Capability.WEB_VISIT, {"status": "denied"}),
        )
        assert result.is_allowed() is False

    def test_evaluate_mapped_capability_web_visit_allowed(self) -> None:
        result = evaluate_mapped_capability(
            "WebVisit",
            (Capability.WEB_VISIT, {"status": "approved"}),
        )
        assert result.is_allowed() is True

    def test_evaluate_mapped_capability_web_visit_denied(self) -> None:
        result = evaluate_mapped_capability(
            "WebVisit",
            (Capability.WEB_VISIT, {"status": "denied"}),
        )
        assert result.is_allowed() is False

    @pytest.mark.parametrize(
        "drain",
        [d for d in SessionDrain if d not in _COMMIT_DRAINS],
    )
    def test_web_visit_granted_to_non_commit_drains(self, drain: SessionDrain) -> None:
        assert CapabilitySet.defaults_for_drain(drain).contains(Capability.WEB_VISIT), (
            f"SessionDrain.{drain.name} is missing Capability.WEB_VISIT"
        )

    @pytest.mark.parametrize("drain", sorted(_COMMIT_DRAINS))
    def test_web_visit_not_granted_to_commit_drains(self, drain: SessionDrain) -> None:
        assert not CapabilitySet.defaults_for_drain(drain).contains(Capability.WEB_VISIT), (
            f"SessionDrain.{drain.name} should not have Capability.WEB_VISIT "
            "(commit-class drains are web-restricted)"
        )
