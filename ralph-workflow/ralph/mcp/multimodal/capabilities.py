"""Multimodal capability detection and delivery policy.

This module is the single source of truth for provider/model identity,
capability detection, and delivery policy decisions. All runtime layers that
need to determine whether a modality can be delivered must derive their answer
from this module rather than re-declaring provider knowledge elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ralph.mcp.multimodal.artifacts import SUPPORTED_MODALITIES


class DeliveryMode(StrEnum):
    """How a multimodal artifact will be delivered to the model."""

    INLINE = "inline"
    RESOURCE_REFERENCE = "resource_reference"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MultimodalModelIdentity:
    """Identifies the provider and model for capability detection."""

    provider: str
    model_id: str | None = None
    transport: str | None = None

    def is_known(self) -> bool:
        """Return True if the provider identity is resolved (not 'unknown')."""
        return self.provider != "unknown"


UNKNOWN_IDENTITY = MultimodalModelIdentity(provider="unknown")


@dataclass(frozen=True)
class CapabilityVerdict:
    """Result of checking whether a modality/delivery mode is supported."""

    modality: str
    delivery: DeliveryMode
    provider: str
    model_id: str | None = None
    reason: str = ""

    def is_inline(self) -> bool:
        """Return True if inline delivery is supported."""
        return self.delivery == DeliveryMode.INLINE

    def is_resource_reference(self) -> bool:
        """Return True if resource-reference delivery will be used."""
        return self.delivery == DeliveryMode.RESOURCE_REFERENCE

    def is_supported(self) -> bool:
        """Return True if the modality has any supported delivery mode."""
        return self.delivery not in {DeliveryMode.UNSUPPORTED}


def _claude_supports_inline_image(model_id: str | None) -> bool:
    return True  # All current Claude models support vision


def _openai_supports_inline_image(model_id: str | None) -> bool:
    if model_id is None:
        return True
    vision_capable_prefixes = ("gpt-4o", "gpt-4-vision", "gpt-4-turbo", "gpt-4", "o1", "o3")
    return any(model_id.startswith(prefix) for prefix in vision_capable_prefixes)


def _gemini_supports_inline_image(model_id: str | None) -> bool:
    return True  # All current Gemini models support vision


def _inline_image_reason(provider: str, model_id: str | None) -> str | None:
    """Return a human-readable reason string if the provider supports inline images."""
    if provider in {"claude", "anthropic"} and _claude_supports_inline_image(model_id):
        return "Claude supports inline image delivery"
    if provider in {"openai", "codex"} and _openai_supports_inline_image(model_id):
        return "OpenAI model supports inline image delivery"
    if provider == "gemini" and _gemini_supports_inline_image(model_id):
        return "Gemini supports inline image delivery"
    return None


def get_delivery_mode(
    identity: MultimodalModelIdentity,
    modality: str,
) -> CapabilityVerdict:
    """Determine how to deliver a modality for the given model identity.

    Returns a CapabilityVerdict indicating whether the modality should be
    delivered inline, via resource_reference, or is unsupported.

    Unknown providers default to resource_reference (safe, keeps multimodal
    surface available without false inline-delivery promises).
    """
    if modality not in SUPPORTED_MODALITIES:
        return CapabilityVerdict(
            modality=modality,
            delivery=DeliveryMode.UNSUPPORTED,
            provider=identity.provider,
            model_id=identity.model_id,
            reason=f"unknown modality '{modality}'",
        )

    if not identity.is_known():
        return CapabilityVerdict(
            modality=modality,
            delivery=DeliveryMode.RESOURCE_REFERENCE,
            provider=identity.provider,
            model_id=identity.model_id,
            reason="unknown provider — defaulting to resource_reference delivery",
        )

    if modality == "image":
        inline_reason = _inline_image_reason(identity.provider.lower(), identity.model_id)
        reason = inline_reason or "provider does not support inline image delivery"
        delivery = DeliveryMode.INLINE if inline_reason else DeliveryMode.RESOURCE_REFERENCE
        return CapabilityVerdict(
            modality=modality,
            delivery=delivery,
            provider=identity.provider,
            model_id=identity.model_id,
            reason=reason,
        )

    # PDF, audio, video, document -> resource_reference for all current providers
    return CapabilityVerdict(
        modality=modality,
        delivery=DeliveryMode.RESOURCE_REFERENCE,
        provider=identity.provider,
        model_id=identity.model_id,
        reason=f"'{modality}' delivered as resource reference",
    )


__all__ = [
    "UNKNOWN_IDENTITY",
    "CapabilityVerdict",
    "DeliveryMode",
    "MultimodalModelIdentity",
    "get_delivery_mode",
]
