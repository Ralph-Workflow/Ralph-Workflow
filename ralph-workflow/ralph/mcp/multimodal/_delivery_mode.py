from __future__ import annotations

from enum import StrEnum


class DeliveryMode(StrEnum):
    INLINE_IMAGE = "inline_image"
    TYPED_BLOCK = "typed_block"
    RESOURCE_REFERENCE_REPLAY = "resource_reference_replay"
    UNSUPPORTED = "unsupported"
