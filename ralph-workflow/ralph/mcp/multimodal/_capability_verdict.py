"""Capability verdict dataclass for multimodal delivery mode checks."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.mcp.multimodal._delivery_mode import DeliveryMode


@dataclass(frozen=True)
class CapabilityVerdict:
    """Result of checking whether a modality/delivery mode is supported."""

    modality: str
    delivery: DeliveryMode
    provider: str
    model_id: str | None = None
    reason: str = ""
    block_type: str | None = None

    def __post_init__(self) -> None:
        """Drop a block type from a verdict that delivers no typed block.

        ``block_type`` names the MCP block the delivery path builds, so
        it means nothing on a verdict that builds no block -- and
        ``verdict_for`` only corrects it when the stored delivery is
        ``TYPED_BLOCK``, which left a rehydrated
        ``resource_reference_replay`` verdict carrying whatever its
        payload said. That string is persisted into the media session
        index and rendered into the NEXT phase's prompt appendix, so a
        session file could put arbitrary text, newlines intact, into the
        instructions handed to an agent.

        Made unrepresentable here rather than filtered at each reader:
        the field is only ever meaningful in one state, so the other
        states should not be constructible.
        """
        if self.delivery is not DeliveryMode.TYPED_BLOCK and self.block_type is not None:
            object.__setattr__(self, "block_type", None)

    def is_inline(self) -> bool:
        """Return True if inline image delivery is used."""
        return self.delivery == DeliveryMode.INLINE_IMAGE

    def is_resource_reference(self) -> bool:
        """Return True if resource-reference replay delivery will be used."""
        return self.delivery == DeliveryMode.RESOURCE_REFERENCE_REPLAY

    def is_typed_block(self) -> bool:
        """Return True if typed block delivery will be used."""
        return self.delivery == DeliveryMode.TYPED_BLOCK

    def is_supported(self) -> bool:
        """Return True if the modality has any supported delivery mode."""
        return self.delivery not in {DeliveryMode.UNSUPPORTED}
