"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import (
    AccessDecision,
    AccessDeniedCode,
    AccessMode,
    Capability,
    DrainClass,
    McpCapability,
    PolicyMode,
    PolicyOutcome,
    PolicyOutcomeStatus,
    SessionDrain,
    _coerce_capability,
    _coerce_mcp_capability,
    _coerce_session_drain,
    _extract_named_value,
    _extract_text_field,
    _normalize_policy_outcome,
    _normalize_token,
    _resolved_policy_status,
    check_mcp_capability_policy,
    drain_class_for_session,
    drain_to_access_mode,
    drain_to_policy_mode,
    evaluate_mapped_capability,
    evaluate_workspace_write,
    lookup_ralph_capability,
    policy_from_outcome,
)
from ralph.prompts.template_variables import CapabilitySet

# =============================================================================
# Helper function tests
# =============================================================================


class TestNormalizeToken:
    def test_lowercases(self) -> None:
        assert _normalize_token("Hello") == "hello"

    def test_replaces_dashes(self) -> None:
        assert _normalize_token("hello-world") == "hello_world"

    def test_replaces_spaces(self) -> None:
        assert _normalize_token("hello world") == "hello_world"

    def test_strips_whitespace(self) -> None:
        assert _normalize_token("  hello  ") == "hello"


class TestExtractTextField:
    def test_from_dict(self) -> None:
        assert _extract_text_field({"name": "test"}, "name") == "test"

    def test_from_object(self) -> None:
        class Obj:
            name = "test"

        assert _extract_text_field(Obj(), "name") == "test"

    def test_missing_from_dict(self) -> None:
        assert _extract_text_field({}, "name") is None

    def test_missing_from_object(self) -> None:
        class Obj:
            pass

        assert _extract_text_field(Obj(), "name") is None

    def test_non_string_returns_none(self) -> None:
        assert _extract_text_field({"name": 123}, "name") is None


class TestExtractNamedValue:
    def test_string_passthrough(self) -> None:
        assert _extract_named_value("test") == "test"

    def test_enum_value(self) -> None:
        class MockEnum:
            value = "test_value"

        assert _extract_named_value(MockEnum()) == "test_value"

    def test_status_field(self) -> None:
        class Obj:
            status = "approved"

        assert _extract_named_value(Obj()) == "approved"

    def test_name_field(self) -> None:
        class Obj:
            name = "my_name"

        assert _extract_named_value(Obj()) == "my_name"

    def test_value_field(self) -> None:
        class Obj:
            value = "my_value"

        assert _extract_named_value(Obj()) == "my_value"


# =============================================================================
# Coerce function tests
# =============================================================================


class TestCoerceSessionDrain:
    def test_session_drain_passthrough(self) -> None:
        assert _coerce_session_drain(SessionDrain.DEVELOPMENT) == SessionDrain.DEVELOPMENT

    def test_planning_string(self) -> None:
        assert _coerce_session_drain("planning") == SessionDrain.PLANNING

    def test_development_string(self) -> None:
        assert _coerce_session_drain("development") == SessionDrain.DEVELOPMENT

    def test_with_underscores(self) -> None:
        assert _coerce_session_drain("development") == SessionDrain.DEVELOPMENT

    def test_case_insensitive(self) -> None:
        assert _coerce_session_drain("DEVELOPMENT") == SessionDrain.DEVELOPMENT

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            _coerce_session_drain("unknown_drain")


class TestCoerceCapability:
    def test_capability_passthrough(self) -> None:
        assert _coerce_capability(Capability.WORKSPACE_READ) == Capability.WORKSPACE_READ

    def test_string_lookup(self) -> None:
        assert _coerce_capability("workspace.read") == Capability.WORKSPACE_READ

    def test_alias_lookup(self) -> None:
        assert _coerce_capability("process_exec_bounded") == Capability.PROCESS_EXEC_BOUNDED

    def test_web_search_alias_lookup(self) -> None:
        assert _coerce_capability("web_search") == Capability.WEB_SEARCH

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            _coerce_capability("unknown_capability")


class TestCoerceMcpCapability:
    def test_mcp_capability_passthrough(self) -> None:
        assert _coerce_mcp_capability(McpCapability.WORKSPACE_READ) == McpCapability.WORKSPACE_READ

    def test_string_lookup(self) -> None:
        assert _coerce_mcp_capability("WorkspaceRead") == McpCapability.WORKSPACE_READ

    def test_alias_with_dots(self) -> None:
        assert _coerce_mcp_capability("workspace.read") == McpCapability.WORKSPACE_READ

    def test_web_search_lookup(self) -> None:
        assert _coerce_mcp_capability("WebSearch") == McpCapability.WEB_SEARCH

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            _coerce_mcp_capability("UnknownCapability")


# =============================================================================
# Policy outcome tests
# =============================================================================


class TestResolvedPolicyStatus:
    def test_approved_values(self) -> None:
        assert _resolved_policy_status({}, "approved", None) == PolicyOutcomeStatus.APPROVED
        assert _resolved_policy_status({}, "allow", None) == PolicyOutcomeStatus.APPROVED
        assert _resolved_policy_status({}, "allowed", None) == PolicyOutcomeStatus.APPROVED

    def test_approved_with_restriction_values(self) -> None:
        assert (
            _resolved_policy_status({}, "approved_with_restriction", None)
            == PolicyOutcomeStatus.APPROVED_WITH_RESTRICTION
        )

    def test_denied_values(self) -> None:
        assert _resolved_policy_status({}, "denied", None) == PolicyOutcomeStatus.DENIED
        assert _resolved_policy_status({}, "deny", None) == PolicyOutcomeStatus.DENIED

    def test_dict_with_reason_denies(self) -> None:
        # Any dict with "reason" key is denied
        assert (
            _resolved_policy_status({"reason": "because"}, "", None) == PolicyOutcomeStatus.DENIED
        )

    def test_reason_present_denies(self) -> None:
        assert _resolved_policy_status({}, "", "because") == PolicyOutcomeStatus.DENIED

    def test_unknown_returns_none(self) -> None:
        assert _resolved_policy_status({}, "unknown_status", None) is None


class TestNormalizePolicyOutcome:
    def test_true_is_approved(self) -> None:
        result = _normalize_policy_outcome(True)
        assert result.status == PolicyOutcomeStatus.APPROVED

    def test_policy_outcome_passthrough(self) -> None:
        outcome = PolicyOutcome(status=PolicyOutcomeStatus.APPROVED)
        assert _normalize_policy_outcome(outcome) == outcome

    def test_dict_approved(self) -> None:
        result = _normalize_policy_outcome({"status": "approved"})
        assert result.status == PolicyOutcomeStatus.APPROVED

    def test_dict_with_reason(self) -> None:
        result = _normalize_policy_outcome({"status": "approved", "reason": "test reason"})
        assert result.status == PolicyOutcomeStatus.APPROVED
        assert result.reason == "test reason"

    def test_unsupported_raises(self) -> None:
        with pytest.raises(ValueError):
            _normalize_policy_outcome("unsupported_value")


# =============================================================================
# Access mode and drain class tests
# =============================================================================


class TestDrainClassForSession:
    def test_planning(self) -> None:
        assert drain_class_for_session("planning") == DrainClass.PLANNING

    def test_development(self) -> None:
        assert drain_class_for_session("development") == DrainClass.DEVELOPMENT

    def test_analysis(self) -> None:
        assert drain_class_for_session("analysis") == DrainClass.ANALYSIS

    def test_review(self) -> None:
        assert drain_class_for_session("review") == DrainClass.REVIEW

    def test_fix(self) -> None:
        assert drain_class_for_session("fix") == DrainClass.FIX

    def test_commit(self) -> None:
        assert drain_class_for_session("commit") == DrainClass.COMMIT

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            drain_class_for_session("unknown")


class TestDrainToAccessMode:
    def test_development_allows_write(self) -> None:
        assert drain_to_access_mode("development") == AccessMode.READ_WRITE

    def test_fix_allows_write(self) -> None:
        assert drain_to_access_mode("fix") == AccessMode.READ_WRITE

    def test_planning_readonly(self) -> None:
        assert drain_to_access_mode("planning") == AccessMode.READ_ONLY

    def test_review_readonly(self) -> None:
        assert drain_to_access_mode("review") == AccessMode.READ_ONLY

    def test_analysis_readonly(self) -> None:
        assert drain_to_access_mode("analysis") == AccessMode.READ_ONLY


class TestDrainToPolicyMode:
    def test_development(self) -> None:
        assert drain_to_policy_mode("development") == PolicyMode.DEVELOPMENT

    def test_fix(self) -> None:
        assert drain_to_policy_mode("fix") == PolicyMode.FIX

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            drain_to_policy_mode("unknown")


# =============================================================================
# AccessDecision tests
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


# =============================================================================
# Policy evaluation function tests
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


class TestEvaluateWorkspaceWrite:
    def test_ephemeral_approved(self) -> None:
        result = evaluate_workspace_write({"status": "approved"}, {"status": "denied"})
        assert result.is_allowed() is True

    def test_tracked_approved(self) -> None:
        result = evaluate_workspace_write({"status": "denied"}, {"status": "approved"})
        assert result.is_allowed() is True

    def test_both_denied(self) -> None:
        result = evaluate_workspace_write({"status": "denied"}, {"status": "denied"})
        assert result.is_allowed() is False


class TestLookupRalphCapability:
    def test_workspace_read(self) -> None:
        result = lookup_ralph_capability("WorkspaceRead")
        assert result == Capability.WORKSPACE_READ

    def test_git_status_read(self) -> None:
        result = lookup_ralph_capability("GitStatusRead")
        assert result == Capability.GIT_STATUS_READ

    def test_web_search(self) -> None:
        result = lookup_ralph_capability("WebSearch")
        assert result == Capability.WEB_SEARCH

    def test_unknown_returns_none(self) -> None:
        result = lookup_ralph_capability("UnknownCapability")
        assert result is None


class TestEvaluateMappedCapability:
    def test_allowed_capability(self) -> None:
        result = evaluate_mapped_capability(
            "WorkspaceRead",
            (Capability.WORKSPACE_READ, {"status": "approved"}),
        )
        assert result.is_allowed() is True

    def test_denied_capability(self) -> None:
        result = evaluate_mapped_capability(
            "WorkspaceRead",
            (Capability.WORKSPACE_READ, {"status": "denied"}),
        )
        assert result.is_allowed() is False

    def test_unknown_capability_string(self) -> None:
        result = evaluate_mapped_capability("UnknownCap", None)
        assert result.is_allowed() is False

    def test_none_mapped_outcome(self) -> None:
        result = evaluate_mapped_capability("WorkspaceRead", None)
        assert result.is_allowed() is False

    def test_web_search_allowed_capability(self) -> None:
        result = evaluate_mapped_capability(
            "WebSearch",
            (Capability.WEB_SEARCH, {"status": "approved"}),
        )
        assert result.is_allowed() is True


class TestCheckMcpCapabilityPolicy:
    def test_workspace_write_any_delegates_to_evaluate_workspace_write(self) -> None:
        result = check_mcp_capability_policy(
            "WorkspaceWriteAny",
            {"status": "approved"},
            {"status": "denied"},
            None,
        )
        assert result.is_allowed() is True

    def test_file_write_delegates_to_evaluate_workspace_write(self) -> None:
        result = check_mcp_capability_policy(
            "FileWrite",
            {"status": "approved"},
            {"status": "denied"},
            None,
        )
        assert result.is_allowed() is True

    def test_workspace_coordination_always_allowed(self) -> None:
        result = check_mcp_capability_policy(
            "WorkspaceCoordination",
            {"status": "denied"},
            {"status": "denied"},
            None,
        )
        assert result.is_allowed() is True

    def test_workspace_read_with_mapped_outcome(self) -> None:
        result = check_mcp_capability_policy(
            "WorkspaceRead",
            {"status": "denied"},
            {"status": "denied"},
            (Capability.WORKSPACE_READ, {"status": "approved"}),
        )
        assert result.is_allowed() is True

    def test_unknown_capability_denied(self) -> None:
        result = check_mcp_capability_policy(
            "UnknownCapability",
            {"status": "denied"},
            {"status": "denied"},
            None,
        )
        assert result.is_allowed() is False

    def test_web_search_delegates_to_mapped_capability(self) -> None:
        result = check_mcp_capability_policy(
            "WebSearch",
            {"status": "denied"},
            {"status": "denied"},
            (Capability.WEB_SEARCH, {"status": "approved"}),
        )
        assert result.is_allowed() is True


class TestWebSearchCapabilitySupport:
    @pytest.mark.parametrize(
        "drain",
        [
            SessionDrain.PLANNING,
            SessionDrain.DEVELOPMENT,
            SessionDrain.DEVELOPMENT_ANALYSIS,
            SessionDrain.DEVELOPMENT_COMMIT,
            SessionDrain.REVIEW,
            SessionDrain.REVIEW_ANALYSIS,
            SessionDrain.FIX,
            SessionDrain.REVIEW_COMMIT,
        ],
    )
    def test_web_search_in_granted_drains(self, drain: SessionDrain) -> None:
        assert CapabilitySet.defaults_for_drain(drain).contains(Capability.WEB_SEARCH)

    @pytest.mark.parametrize("drain", [SessionDrain.ANALYSIS, SessionDrain.COMMIT])
    def test_web_search_not_granted_to_other_drains(self, drain: SessionDrain) -> None:
        assert not CapabilitySet.defaults_for_drain(drain).contains(Capability.WEB_SEARCH)
