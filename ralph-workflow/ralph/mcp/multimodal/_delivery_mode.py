"""Delivery mode enum for multimodal artifact delivery."""

from __future__ import annotations

from enum import StrEnum


class DeliveryMode(StrEnum):
    """How a multimodal artifact will be delivered to the model."""

    INLINE_IMAGE = "inline_image"
    TYPED_BLOCK = "typed_block"
    RESOURCE_REFERENCE_REPLAY = "resource_reference_replay"
    UNSUPPORTED = "unsupported"
