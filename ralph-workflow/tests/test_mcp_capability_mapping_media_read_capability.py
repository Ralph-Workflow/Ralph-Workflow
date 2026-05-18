"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

from ralph.mcp.protocol.capability_mapping import (
    MCP_TO_RALPH_CAPABILITY_MAP,
    Capability,
    McpCapability,
    check_mcp_capability_policy,
    coerce_capability,
    coerce_mcp_capability,
    evaluate_mapped_capability,
    lookup_ralph_capability,
)

# =============================================================================
# Helper function tests
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
        """coerce_capability accepts 'media.read' and returns Capability.MEDIA_READ."""
        result = coerce_capability("media.read")
        assert result == Capability.MEDIA_READ

    def test_media_read_alias_underscore_notation_in_coerce_capability(self) -> None:
        """coerce_capability accepts 'media_read' and returns Capability.MEDIA_READ."""
        result = coerce_capability("media_read")
        assert result == Capability.MEDIA_READ

    def test_media_read_alias_dot_notation_in_coerce_mcp_capability(self) -> None:
        """coerce_mcp_capability accepts 'media.read' and returns McpCapability.MEDIA_READ."""
        result = coerce_mcp_capability("media.read")
        assert result == McpCapability.MEDIA_READ

    def test_media_read_alias_underscore_notation_in_coerce_mcp_capability(self) -> None:
        """coerce_mcp_capability accepts 'media_read' and returns McpCapability.MEDIA_READ."""
        result = coerce_mcp_capability("media_read")
        assert result == McpCapability.MEDIA_READ

    def test_media_read_alias_capitalized_in_coerce_mcp_capability(self) -> None:
        """coerce_mcp_capability accepts 'MediaRead' and returns McpCapability.MEDIA_READ."""
        result = coerce_mcp_capability("MediaRead")
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
