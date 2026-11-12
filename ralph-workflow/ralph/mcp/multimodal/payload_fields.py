from __future__ import annotations

from ralph.mcp.multimodal._delivery_mode import DeliveryMode
from ralph.mcp.multimodal._multimodal_model_identity import MultimodalModelIdentity

_UNKNOWN_PROVIDER = MultimodalModelIdentity(provider="").provider


def payload_transport(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    return raw.strip().lower() or None


def payload_provider(raw: object) -> str:
    return raw if isinstance(raw, str) else _UNKNOWN_PROVIDER


def payload_model_id(raw: object) -> str | None:
    return raw if isinstance(raw, str) else None


def payload_delivery(raw: object) -> DeliveryMode:
    if not isinstance(raw, str):
        return DeliveryMode.RESOURCE_REFERENCE_REPLAY
    try:
        return DeliveryMode(raw)
    except ValueError:
        return DeliveryMode.RESOURCE_REFERENCE_REPLAY


def payload_identity(
    provider: object, model_id: object, transport: object
) -> MultimodalModelIdentity:
    return MultimodalModelIdentity(
        provider=payload_provider(provider),
        model_id=payload_model_id(model_id),
        transport=payload_transport(transport),
    )


__all__ = [
    "payload_delivery",
    "payload_identity",
    "payload_model_id",
    "payload_provider",
    "payload_transport",
]
