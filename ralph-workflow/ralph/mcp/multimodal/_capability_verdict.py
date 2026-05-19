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
