"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import (
    MCP_TO_RALPH_CAPABILITY_MAP,
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
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy
from ralph.policy.validation import PolicyValidationError
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


def _builtin_agents_policy() -> AgentsPolicy:
    return AgentsPolicy(
        agent_chains={"default": AgentChainConfig(agents=["agent"])},
        agent_drains={
            "planning": AgentDrainConfig(chain="default", drain_class="planning"),
            "development": AgentDrainConfig(chain="default", drain_class="development"),
            "analysis": AgentDrainConfig(chain="default", drain_class="analysis"),
            "review": AgentDrainConfig(chain="default", drain_class="review"),
            "fix": AgentDrainConfig(chain="default", drain_class="fix"),
            "commit": AgentDrainConfig(chain="default", drain_class="commit"),
        },
    )


class TestDrainClassForSession:
    def test_planning(self) -> None:
        assert drain_class_for_session(
            "planning", _builtin_agents_policy()
        ) == DrainClass.PLANNING

    def test_development(self) -> None:
        assert drain_class_for_session(
            "development", _builtin_agents_policy()
        ) == DrainClass.DEVELOPMENT

    def test_analysis(self) -> None:
        assert drain_class_for_session(
            "analysis", _builtin_agents_policy()
        ) == DrainClass.ANALYSIS

    def test_review(self) -> None:
        assert drain_class_for_session(
            "review", _builtin_agents_policy()
        ) == DrainClass.REVIEW

    def test_fix(self) -> None:
        assert drain_class_for_session("fix", _builtin_agents_policy()) == DrainClass.FIX

    def test_commit(self) -> None:
        assert drain_class_for_session("commit", _builtin_agents_policy()) == DrainClass.COMMIT

    def test_unknown_raises(self) -> None:
        with pytest.raises(PolicyValidationError):
            drain_class_for_session("unknown")


class TestDrainToAccessMode:
    def test_development_allows_write(self) -> None:
        assert drain_to_access_mode(
            "development", _builtin_agents_policy()
        ) == AccessMode.READ_WRITE

    def test_fix_allows_write(self) -> None:
        assert drain_to_access_mode("fix", _builtin_agents_policy()) == AccessMode.READ_WRITE

    def test_planning_readonly(self) -> None:
        assert drain_to_access_mode("planning", _builtin_agents_policy()) == AccessMode.READ_ONLY

    def test_review_readonly(self) -> None:
        assert drain_to_access_mode("review", _builtin_agents_policy()) == AccessMode.READ_ONLY

    def test_analysis_readonly(self) -> None:
        assert drain_to_access_mode("analysis", _builtin_agents_policy()) == AccessMode.READ_ONLY


class TestDrainToPolicyMode:
    def test_development(self) -> None:
        assert drain_to_policy_mode(
            "development", _builtin_agents_policy()
        ) == PolicyMode.DEVELOPMENT

    def test_fix(self) -> None:
        assert drain_to_policy_mode("fix", _builtin_agents_policy()) == PolicyMode.FIX

    def test_unknown_raises_policy_validation_error(self) -> None:
        with pytest.raises(PolicyValidationError):
            drain_to_policy_mode("unknown")

    def test_custom_drain_with_explicit_drain_class(self) -> None:
        policy = AgentsPolicy(
            agent_chains={"c": AgentChainConfig(agents=["agent"])},
            agent_drains={"my_custom_audit": AgentDrainConfig(chain="c", drain_class="analysis")},
        )
        assert drain_to_policy_mode("my_custom_audit", policy) == PolicyMode.ANALYSIS

    def test_custom_commit_drain_without_policy_raises(self) -> None:
        with pytest.raises(PolicyValidationError):
            drain_to_policy_mode("feature_commit")


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


# =============================================================================
# MediaRead capability tests (Task 2)
# =============================================================================


class TestMediaReadCapability:
    """Tests for MediaRead capability mapping (Task 2)."""

    def test_capability_media_read_exists(self) -> None:
        """Capability.MEDIA_READ exists with value 'media.read'."""
        assert hasattr(Capability, "MEDIA_READ")
        assert Capability.MEDIA_READ == "media.read"

    def test_mcp_capability_media_read_exists(self) -> None:
        """McpCapability.MEDIA_READ exists with value 'MediaRead'."""
        assert hasattr(McpCapability, "MEDIA_READ")
        assert McpCapability.MEDIA_READ == "MediaRead"

    def test_media_read_alias_dot_notation_in_coerce_capability(self) -> None:
        """_coerce_capability accepts 'media.read' and returns Capability.MEDIA_READ."""
        result = _coerce_capability("media.read")
        assert result == Capability.MEDIA_READ

    def test_media_read_alias_underscore_notation_in_coerce_capability(self) -> None:
        """_coerce_capability accepts 'media_read' and returns Capability.MEDIA_READ."""
        result = _coerce_capability("media_read")
        assert result == Capability.MEDIA_READ

    def test_media_read_alias_dot_notation_in_coerce_mcp_capability(self) -> None:
        """_coerce_mcp_capability accepts 'media.read' and returns McpCapability.MEDIA_READ."""
        result = _coerce_mcp_capability("media.read")
        assert result == McpCapability.MEDIA_READ

    def test_media_read_alias_underscore_notation_in_coerce_mcp_capability(self) -> None:
        """_coerce_mcp_capability accepts 'media_read' and returns McpCapability.MEDIA_READ."""
        result = _coerce_mcp_capability("media_read")
        assert result == McpCapability.MEDIA_READ

    def test_media_read_alias_capitalized_in_coerce_mcp_capability(self) -> None:
        """_coerce_mcp_capability accepts 'MediaRead' and returns McpCapability.MEDIA_READ."""
        result = _coerce_mcp_capability("MediaRead")
        assert result == McpCapability.MEDIA_READ

    def test_media_read_maps_to_ralph_capability(self) -> None:
        """lookup_ralph_capability('MediaRead') returns Capability.MEDIA_READ."""
        result = lookup_ralph_capability("MediaRead")
        assert result == Capability.MEDIA_READ

    def test_media_read_in_mcp_to_ralph_map(self) -> None:
        """MCP_TO_RALPH_CAPABILITY_MAP maps McpCapability.MEDIA_READ to Capability.MEDIA_READ."""
        assert MCP_TO_RALPH_CAPABILITY_MAP[McpCapability.MEDIA_READ] == Capability.MEDIA_READ

    def test_media_read_policy_allowed(self) -> None:
        """check_mcp_capability_policy for MediaRead with approved outcome is allowed."""
        result = check_mcp_capability_policy(
            "MediaRead",
            {"status": "denied"},
            {"status": "denied"},
            (Capability.MEDIA_READ, {"status": "approved"}),
        )
        assert result.is_allowed() is True

    def test_media_read_policy_denied(self) -> None:
        """check_mcp_capability_policy for MediaRead with denied outcome is denied."""
        result = check_mcp_capability_policy(
            "MediaRead",
            {"status": "denied"},
            {"status": "denied"},
            (Capability.MEDIA_READ, {"status": "denied"}),
        )
        assert result.is_allowed() is False

    def test_evaluate_mapped_capability_media_read_allowed(self) -> None:
        """evaluate_mapped_capability works for MediaRead with approved outcome."""
        result = evaluate_mapped_capability(
            "MediaRead",
            (Capability.MEDIA_READ, {"status": "approved"}),
        )
        assert result.is_allowed() is True

    def test_evaluate_mapped_capability_media_read_denied(self) -> None:
        """evaluate_mapped_capability works for MediaRead with denied outcome."""
        result = evaluate_mapped_capability(
            "MediaRead",
            (Capability.MEDIA_READ, {"status": "denied"}),
        )
        assert result.is_allowed() is False


# =============================================================================
# WebVisit capability tests
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
        result = _coerce_capability("web.visit")
        assert result == Capability.WEB_VISIT

    def test_web_visit_alias_underscore_notation_in_coerce_capability(self) -> None:
        result = _coerce_capability("web_visit")
        assert result == Capability.WEB_VISIT

    def test_web_visit_alias_dot_notation_in_coerce_mcp_capability(self) -> None:
        result = _coerce_mcp_capability("web.visit")
        assert result == McpCapability.WEB_VISIT

    def test_web_visit_alias_underscore_notation_in_coerce_mcp_capability(self) -> None:
        result = _coerce_mcp_capability("web_visit")
        assert result == McpCapability.WEB_VISIT

    def test_web_visit_alias_capitalized_in_coerce_mcp_capability(self) -> None:
        result = _coerce_mcp_capability("WebVisit")
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

    @pytest.mark.parametrize("drain", list(SessionDrain))
    def test_web_visit_granted_to_all_drains(self, drain: SessionDrain) -> None:
        assert CapabilitySet.defaults_for_drain(drain).contains(Capability.WEB_VISIT), (
            f"SessionDrain.{drain.name} is missing Capability.WEB_VISIT"
        )


# =============================================================================
# WorkspaceEdit, WorkspaceDelete, WorkspaceMetadataRead capability tests
# =============================================================================


class TestWorkspaceNewCapabilities:
    """Tests for WorkspaceEdit, WorkspaceDelete, and WorkspaceMetadataRead capability mapping."""

    # ------------------------------------------------------------------
    # WorkspaceEdit
    # ------------------------------------------------------------------

    def test_workspace_edit_capability_exists(self) -> None:
        assert hasattr(Capability, "WORKSPACE_EDIT")
        assert Capability.WORKSPACE_EDIT == "workspace.edit"

    def test_mcp_workspace_edit_exists(self) -> None:
        assert hasattr(McpCapability, "WORKSPACE_EDIT")
        assert McpCapability.WORKSPACE_EDIT == "WorkspaceEdit"

    def test_workspace_edit_in_mcp_to_ralph_map(self) -> None:
        assert (
            MCP_TO_RALPH_CAPABILITY_MAP[McpCapability.WORKSPACE_EDIT]
            == Capability.WORKSPACE_EDIT
        )

    def test_lookup_ralph_capability_workspace_edit(self) -> None:
        result = lookup_ralph_capability("WorkspaceEdit")
        assert result == Capability.WORKSPACE_EDIT

    def test_coerce_mcp_capability_workspace_edit_dot(self) -> None:
        assert _coerce_mcp_capability("workspace.edit") == McpCapability.WORKSPACE_EDIT

    def test_coerce_mcp_capability_workspace_edit_string(self) -> None:
        assert _coerce_mcp_capability("WorkspaceEdit") == McpCapability.WORKSPACE_EDIT

    def test_workspace_edit_policy_allowed(self) -> None:
        result = check_mcp_capability_policy(
            "WorkspaceEdit",
            {"status": "denied"},
            {"status": "denied"},
            (Capability.WORKSPACE_EDIT, PolicyOutcome(status=PolicyOutcomeStatus.APPROVED)),
        )
        assert result.is_allowed() is True

    def test_workspace_edit_policy_denied_with_reason(self) -> None:
        outcome = PolicyOutcome(status=PolicyOutcomeStatus.DENIED, reason="not granted")
        result = check_mcp_capability_policy(
            "WorkspaceEdit",
            {"status": "denied"},
            {"status": "denied"},
            (Capability.WORKSPACE_EDIT, outcome),
        )
        assert result.is_allowed() is False
        assert result.code == AccessDeniedCode.CAPABILITY_DENIED

    def test_workspace_edit_policy_no_mapped_outcome(self) -> None:
        result = check_mcp_capability_policy(
            "WorkspaceEdit",
            {"status": "denied"},
            {"status": "denied"},
            None,
        )
        assert result.is_allowed() is False
        assert result.code == AccessDeniedCode.CAPABILITY_DENIED

    # ------------------------------------------------------------------
    # WorkspaceDelete
    # ------------------------------------------------------------------

    def test_workspace_delete_capability_exists(self) -> None:
        assert hasattr(Capability, "WORKSPACE_DELETE")
        assert Capability.WORKSPACE_DELETE == "workspace.delete"

    def test_mcp_workspace_delete_exists(self) -> None:
        assert hasattr(McpCapability, "WORKSPACE_DELETE")
        assert McpCapability.WORKSPACE_DELETE == "WorkspaceDelete"

    def test_workspace_delete_in_mcp_to_ralph_map(self) -> None:
        assert (
            MCP_TO_RALPH_CAPABILITY_MAP[McpCapability.WORKSPACE_DELETE]
            == Capability.WORKSPACE_DELETE
        )

    def test_lookup_ralph_capability_workspace_delete(self) -> None:
        result = lookup_ralph_capability("WorkspaceDelete")
        assert result == Capability.WORKSPACE_DELETE

    def test_coerce_mcp_capability_workspace_delete_dot(self) -> None:
        assert _coerce_mcp_capability("workspace.delete") == McpCapability.WORKSPACE_DELETE

    def test_coerce_mcp_capability_workspace_delete_string(self) -> None:
        assert _coerce_mcp_capability("WorkspaceDelete") == McpCapability.WORKSPACE_DELETE

    def test_workspace_delete_policy_allowed(self) -> None:
        result = check_mcp_capability_policy(
            "WorkspaceDelete",
            {"status": "denied"},
            {"status": "denied"},
            (Capability.WORKSPACE_DELETE, PolicyOutcome(status=PolicyOutcomeStatus.APPROVED)),
        )
        assert result.is_allowed() is True

    def test_workspace_delete_policy_denied_with_reason(self) -> None:
        outcome = PolicyOutcome(status=PolicyOutcomeStatus.DENIED, reason="not granted")
        result = check_mcp_capability_policy(
            "WorkspaceDelete",
            {"status": "denied"},
            {"status": "denied"},
            (Capability.WORKSPACE_DELETE, outcome),
        )
        assert result.is_allowed() is False
        assert result.code == AccessDeniedCode.CAPABILITY_DENIED

    def test_workspace_delete_policy_no_mapped_outcome(self) -> None:
        result = check_mcp_capability_policy(
            "WorkspaceDelete",
            {"status": "denied"},
            {"status": "denied"},
            None,
        )
        assert result.is_allowed() is False
        assert result.code == AccessDeniedCode.CAPABILITY_DENIED

    # ------------------------------------------------------------------
    # WorkspaceMetadataRead
    # ------------------------------------------------------------------

    def test_workspace_metadata_read_capability_exists(self) -> None:
        assert hasattr(Capability, "WORKSPACE_METADATA_READ")
        assert Capability.WORKSPACE_METADATA_READ == "workspace.metadata_read"

    def test_mcp_workspace_metadata_read_exists(self) -> None:
        assert hasattr(McpCapability, "WORKSPACE_METADATA_READ")
        assert McpCapability.WORKSPACE_METADATA_READ == "WorkspaceMetadataRead"

    def test_workspace_metadata_read_in_mcp_to_ralph_map(self) -> None:
        assert (
            MCP_TO_RALPH_CAPABILITY_MAP[McpCapability.WORKSPACE_METADATA_READ]
            == Capability.WORKSPACE_METADATA_READ
        )

    def test_lookup_ralph_capability_workspace_metadata_read(self) -> None:
        result = lookup_ralph_capability("WorkspaceMetadataRead")
        assert result == Capability.WORKSPACE_METADATA_READ

    def test_coerce_mcp_capability_workspace_metadata_read_dot(self) -> None:
        assert (
            _coerce_mcp_capability("workspace.metadata_read")
            == McpCapability.WORKSPACE_METADATA_READ
        )

    def test_coerce_mcp_capability_workspace_metadata_read_string(self) -> None:
        assert (
            _coerce_mcp_capability("WorkspaceMetadataRead")
            == McpCapability.WORKSPACE_METADATA_READ
        )

    def test_workspace_metadata_read_policy_allowed(self) -> None:
        result = check_mcp_capability_policy(
            "WorkspaceMetadataRead",
            {"status": "denied"},
            {"status": "denied"},
            (
                Capability.WORKSPACE_METADATA_READ,
                PolicyOutcome(status=PolicyOutcomeStatus.APPROVED),
            ),
        )
        assert result.is_allowed() is True

    def test_workspace_metadata_read_policy_denied_with_reason(self) -> None:
        outcome = PolicyOutcome(status=PolicyOutcomeStatus.DENIED, reason="not granted")
        result = check_mcp_capability_policy(
            "WorkspaceMetadataRead",
            {"status": "denied"},
            {"status": "denied"},
            (Capability.WORKSPACE_METADATA_READ, outcome),
        )
        assert result.is_allowed() is False
        assert result.code == AccessDeniedCode.CAPABILITY_DENIED

    def test_workspace_metadata_read_policy_no_mapped_outcome(self) -> None:
        result = check_mcp_capability_policy(
            "WorkspaceMetadataRead",
            {"status": "denied"},
            {"status": "denied"},
            None,
        )
        assert result.is_allowed() is False
        assert result.code == AccessDeniedCode.CAPABILITY_DENIED

    # ------------------------------------------------------------------
    # Drain-coverage: metadata_read (all drains), edit/delete (dev/fix only)
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("drain", list(SessionDrain))
    def test_workspace_metadata_read_granted_to_all_drains(self, drain: SessionDrain) -> None:
        assert CapabilitySet.defaults_for_drain(drain).contains(
            Capability.WORKSPACE_METADATA_READ
        ), f"SessionDrain.{drain.name} is missing Capability.WORKSPACE_METADATA_READ"

    @pytest.mark.parametrize("drain", list(SessionDrain))
    def test_workspace_edit_granted_only_to_dev_and_fix(self, drain: SessionDrain) -> None:
        expected = drain in {SessionDrain.DEVELOPMENT, SessionDrain.FIX}
        granted = CapabilitySet.defaults_for_drain(drain).contains(Capability.WORKSPACE_EDIT)
        assert granted is expected, (
            f"SessionDrain.{drain.name}: WORKSPACE_EDIT grant expected={expected}"
        )

    @pytest.mark.parametrize("drain", list(SessionDrain))
    def test_workspace_delete_granted_only_to_dev_and_fix(self, drain: SessionDrain) -> None:
        expected = drain in {SessionDrain.DEVELOPMENT, SessionDrain.FIX}
        granted = CapabilitySet.defaults_for_drain(drain).contains(Capability.WORKSPACE_DELETE)
        assert granted is expected, (
            f"SessionDrain.{drain.name}: WORKSPACE_DELETE grant expected={expected}"
        )
