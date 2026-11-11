
from __future__ import annotations

from dataclasses import dataclass

from ralph.mcp.multimodal._delivery_mode import DeliveryMode


@dataclass(frozen=True)
class CapabilityVerdict:

    modality: str
    delivery: DeliveryMode
    provider: str
    model_id: str | None = None
    reason: str = ""
    block_type: str | None = None

    def __post_init__(self) -> None:
        if self.delivery is not DeliveryMode.TYPED_BLOCK and self.block_type is not None:
            object.__setattr__(self, "block_type", None)

    def is_inline(self) -> bool:
        return self.delivery == DeliveryMode.INLINE_IMAGE

    def is_resource_reference(self) -> bool:
        return self.delivery == DeliveryMode.RESOURCE_REFERENCE_REPLAY

    def is_typed_block(self) -> bool:
        return self.delivery == DeliveryMode.TYPED_BLOCK

    def is_supported(self) -> bool:
        return self.delivery not in {DeliveryMode.UNSUPPORTED}
