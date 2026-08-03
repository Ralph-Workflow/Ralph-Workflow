"""Tests for drain identity handling in capability mapping."""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import (
    Capability,
    DrainClass,
    McpCapability,
    PolicyMode,
    SessionDrain,
    drain_class_for_session,
    drain_to_policy_mode,
    lookup_ralph_capability,
)
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy
from ralph.policy.validation import PolicyValidationError


class TestNewCapabilities:
    """Tests for new workspace capabilities."""

    def test_lookup_workspace_metadata_read_capability(self) -> None:
        """WorkspaceMetadataRead maps to workspace.metadata_read."""
        result = lookup_ralph_capability(McpCapability.WORKSPACE_METADATA_READ)
        assert result == Capability.WORKSPACE_METADATA_READ

    def test_lookup_workspace_edit_capability(self) -> None:
        """WorkspaceEdit maps to workspace.edit."""
        result = lookup_ralph_capability(McpCapability.WORKSPACE_EDIT)
        assert result == Capability.WORKSPACE_EDIT

    def test_lookup_workspace_delete_capability(self) -> None:
        """WorkspaceDelete maps to workspace.delete."""
        result = lookup_ralph_capability(McpCapability.WORKSPACE_DELETE)
        assert result == Capability.WORKSPACE_DELETE

    def test_lookup_capability_alias_metadata_read(self) -> None:
        """workspace.metadata_read alias resolves correctly."""
        result = lookup_ralph_capability("workspace.metadata_read")
        assert result == Capability.WORKSPACE_METADATA_READ

    def test_lookup_capability_alias_edit(self) -> None:
        """workspace.edit alias resolves correctly."""
        result = lookup_ralph_capability("workspace.edit")
        assert result == Capability.WORKSPACE_EDIT

    def test_lookup_capability_alias_delete(self) -> None:
        """workspace.delete alias resolves correctly."""
        result = lookup_ralph_capability("workspace.delete")
        assert result == Capability.WORKSPACE_DELETE

    def test_lookup_nonexistent_capability_returns_none(self) -> None:
        """Unknown capability strings return None."""
        result = lookup_ralph_capability("nonexistent.capability")
        assert result is None
