"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import (
    MCP_TO_RALPH_CAPABILITY_MAP,
    AccessDeniedCode,
    Capability,
    McpCapability,
    PolicyOutcome,
    PolicyOutcomeStatus,
    SessionDrain,
    check_mcp_capability_policy,
    coerce_mcp_capability,
    lookup_ralph_capability,
)
from ralph.prompts.template_variables import CapabilitySet

# =============================================================================
# Helper function tests
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
            MCP_TO_RALPH_CAPABILITY_MAP[McpCapability.WORKSPACE_EDIT] == Capability.WORKSPACE_EDIT
        )

    def test_lookup_ralph_capability_workspace_edit(self) -> None:
        result = lookup_ralph_capability("WorkspaceEdit")
        assert result == Capability.WORKSPACE_EDIT

    def test_coerce_mcp_capability_workspace_edit_dot(self) -> None:
        assert coerce_mcp_capability("workspace.edit") == McpCapability.WORKSPACE_EDIT

    def test_coerce_mcp_capability_workspace_edit_string(self) -> None:
        assert coerce_mcp_capability("WorkspaceEdit") == McpCapability.WORKSPACE_EDIT

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
        assert coerce_mcp_capability("workspace.delete") == McpCapability.WORKSPACE_DELETE

    def test_coerce_mcp_capability_workspace_delete_string(self) -> None:
        assert coerce_mcp_capability("WorkspaceDelete") == McpCapability.WORKSPACE_DELETE

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
            coerce_mcp_capability("workspace.metadata_read")
            == McpCapability.WORKSPACE_METADATA_READ
        )

    def test_coerce_mcp_capability_workspace_metadata_read_string(self) -> None:
        assert (
            coerce_mcp_capability("WorkspaceMetadataRead") == McpCapability.WORKSPACE_METADATA_READ
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
