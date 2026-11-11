from __future__ import annotations

from ralph.mcp.protocol.capability_mapping import (
    Capability,
    McpCapability,
    lookup_ralph_capability,
)


class TestNewCapabilities:
    def test_lookup_workspace_metadata_read_capability(self) -> None:
        result = lookup_ralph_capability(McpCapability.WORKSPACE_METADATA_READ)
        assert result == Capability.WORKSPACE_METADATA_READ

    def test_lookup_workspace_edit_capability(self) -> None:
        result = lookup_ralph_capability(McpCapability.WORKSPACE_EDIT)
        assert result == Capability.WORKSPACE_EDIT

    def test_lookup_workspace_delete_capability(self) -> None:
        result = lookup_ralph_capability(McpCapability.WORKSPACE_DELETE)
        assert result == Capability.WORKSPACE_DELETE

    def test_lookup_capability_alias_metadata_read(self) -> None:
        result = lookup_ralph_capability("workspace.metadata_read")
        assert result == Capability.WORKSPACE_METADATA_READ

    def test_lookup_capability_alias_edit(self) -> None:
        result = lookup_ralph_capability("workspace.edit")
        assert result == Capability.WORKSPACE_EDIT

    def test_lookup_capability_alias_delete(self) -> None:
        result = lookup_ralph_capability("workspace.delete")
        assert result == Capability.WORKSPACE_DELETE

    def test_lookup_nonexistent_capability_returns_none(self) -> None:
        result = lookup_ralph_capability("nonexistent.capability")
        assert result is None
