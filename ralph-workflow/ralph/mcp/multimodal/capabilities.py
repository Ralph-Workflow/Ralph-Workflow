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

# ---------------------------------------------------------------------------
# Per-provider inline-image support
# ---------------------------------------------------------------------------


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


# Typed-block support per provider and modality.
# Maps (provider, modality) -> block_type string for TYPED_BLOCK delivery.
_TYPED_BLOCK_SUPPORT: dict[str, dict[str, str]] = {
    "claude": {"pdf": "pdf", "document": "document"},
    "anthropic": {"pdf": "pdf", "document": "document"},
    "gemini": {"pdf": "pdf", "document": "document", "audio": "audio", "video": "video"},
}


# ---------------------------------------------------------------------------
# Per-provider non-image modality support matrix
# ---------------------------------------------------------------------------

# Modalities explicitly unsupported for each known provider via Ralph's
# managed MCP runtime path. Providers not listed here fall through to the
# safe resource_reference default.
#
# UNSUPPORTED means Ralph cannot deliver the modality through its managed
# path for this provider — the model API simply does not accept it.
# RESOURCE_REFERENCE means the agent can retrieve the bytes via resources/read
# and attempt to relay them to the model in a provider-appropriate form.
_PROVIDER_UNSUPPORTED_MODALITIES: dict[str, frozenset[str]] = {
    # Claude/Anthropic does not accept audio or video input via its API.
    # Images and PDFs are deliverable (inline or via document blocks).
    # Documents (.docx, .pptx, .xlsx) are accepted via document blocks on
    # models that support them.
    "claude": frozenset({"audio", "video"}),
    "anthropic": frozenset({"audio", "video"}),
    # OpenAI chat completion API does not accept PDFs, documents, audio, or
    # video as raw bytes through Ralph's managed MCP path. Only images are
    # supported (for vision-capable models). Marking pdf/document/audio/video
    # as UNSUPPORTED so the agent receives an explicit failure instead of a
    # resource_reference that the model cannot process.
    "openai": frozenset({"audio", "video", "pdf", "document"}),
    "codex": frozenset({"audio", "video", "pdf", "document"}),
    # Gemini supports audio, video, PDFs, and documents natively;
    # no modalities are unsupported.
    "gemini": frozenset(),
}

_PROVIDER_UNSUPPORTED_REASON: dict[str, str] = {
    "claude": "Claude does not accept this modality via Ralph's managed MCP path",
    "anthropic": "Anthropic does not accept this modality via Ralph's managed MCP path",
    "openai": "OpenAI does not accept this modality via Ralph's managed MCP path",
    "codex": "Codex does not accept this modality via Ralph's managed MCP path",
}


def get_delivery_mode(
    identity: MultimodalModelIdentity,
    modality: str,
) -> CapabilityVerdict:
    """Determine how to deliver a modality for the given model identity.

    Returns a CapabilityVerdict indicating the delivery mode:

    - INLINE_IMAGE: provider accepts inline base64 image data.
    - TYPED_BLOCK: provider accepts a named typed block (pdf, document, audio, video).
    - RESOURCE_REFERENCE_REPLAY: unknown provider; multimodal surface stays visible
      via resource reference replay handle.
    - UNSUPPORTED: provider cannot accept this modality via Ralph's managed path.

    Unknown providers default to RESOURCE_REFERENCE_REPLAY (safe, keeps multimodal
    surface available without false typed-delivery promises).
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
            delivery=DeliveryMode.RESOURCE_REFERENCE_REPLAY,
            provider=identity.provider,
            model_id=identity.model_id,
            reason="unknown provider — defaulting to resource_reference_replay delivery",
        )

    provider_lower = identity.provider.lower()

    if modality == "image":
        inline_reason = _inline_image_reason(provider_lower, identity.model_id)
        delivery = (
            DeliveryMode.INLINE_IMAGE if inline_reason else DeliveryMode.RESOURCE_REFERENCE_REPLAY
        )
        reason = inline_reason or "provider does not support inline image delivery"
        return CapabilityVerdict(
            modality=modality,
            delivery=delivery,
            provider=identity.provider,
            model_id=identity.model_id,
            reason=reason,
        )

    # Check whether this provider explicitly does not support this modality.
    unsupported = _PROVIDER_UNSUPPORTED_MODALITIES.get(provider_lower, frozenset())
    if modality in unsupported:
        base_reason = _PROVIDER_UNSUPPORTED_REASON.get(
            provider_lower,
            f"provider '{identity.provider}' does not support '{modality}'",
        )
        return CapabilityVerdict(
            modality=modality,
            delivery=DeliveryMode.UNSUPPORTED,
            provider=identity.provider,
            model_id=identity.model_id,
            reason=f"{base_reason} (modality: {modality})",
        )

    # Typed-block or resource_reference_replay for remaining known-provider modalities.
    typed_blocks = _TYPED_BLOCK_SUPPORT.get(provider_lower, {})
    block_type: str | None = typed_blocks.get(modality)
    delivery = DeliveryMode.TYPED_BLOCK if block_type else DeliveryMode.RESOURCE_REFERENCE_REPLAY
    reason = (
        f"'{modality}' delivered as typed block '{block_type}' for provider '{identity.provider}'"
        if block_type
        else f"'{modality}' as resource_reference_replay for provider '{identity.provider}'"
    )
    return CapabilityVerdict(
        modality=modality,
        delivery=delivery,
        provider=identity.provider,
        model_id=identity.model_id,
        reason=reason,
        block_type=block_type,
    )


@dataclass
class ResolvedCapabilityProfile:
    """Pre-computed capability verdicts for a resolved model identity.

    This is the runtime-owned contract for multimodal delivery decisions.
    Downstream layers consume this profile from the session rather than
    re-calling get_delivery_mode() at each use site.
    """

    class DeliveryMode(StrEnum):
        """How a multimodal artifact will be delivered to the model."""

        INLINE_IMAGE = "inline_image"
        TYPED_BLOCK = "typed_block"
        RESOURCE_REFERENCE_REPLAY = "resource_reference_replay"
        UNSUPPORTED = "unsupported"

    @dataclass(frozen=True)
    class MultimodalModelIdentity:
        """Identifies the provider and model for capability detection."""

        provider: str
        model_id: str | None = None
        transport: str | None = None

        def is_known(self) -> bool:
            """Return True if the provider identity is resolved (not 'unknown')."""
            return self.provider != "unknown"

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


    identity: MultimodalModelIdentity
    verdicts: dict[str, CapabilityVerdict]

    def verdict_for(self, modality: str) -> CapabilityVerdict:
        """Return the pre-computed verdict, or compute fresh for unlisted modalities."""
        if modality in self.verdicts:
            return self.verdicts[modality]
        return get_delivery_mode(self.identity, modality)

    def to_payload(self) -> dict[str, object]:
        """Serialize to a JSON-compatible dict for session payload persistence."""
        return {
            "provider": self.identity.provider,
            "model_id": self.identity.model_id,
            "transport": self.identity.transport,
            "verdicts": {
                modality: {
                    "delivery": v.delivery.value,
                    "reason": v.reason,
                    "block_type": v.block_type,
                }
                for modality, v in self.verdicts.items()
            },
        }


DeliveryMode = ResolvedCapabilityProfile.DeliveryMode
MultimodalModelIdentity = ResolvedCapabilityProfile.MultimodalModelIdentity
CapabilityVerdict = ResolvedCapabilityProfile.CapabilityVerdict

UNKNOWN_IDENTITY = MultimodalModelIdentity(provider="unknown")


def resolve_capability_profile(identity: MultimodalModelIdentity) -> ResolvedCapabilityProfile:
    """Build a pre-computed capability profile for all supported modalities."""
    verdicts = {
        modality: get_delivery_mode(identity, modality) for modality in SUPPORTED_MODALITIES
    }
    return ResolvedCapabilityProfile(identity=identity, verdicts=verdicts)


def profile_from_payload(raw: dict[str, object]) -> ResolvedCapabilityProfile:
    """Rehydrate a ResolvedCapabilityProfile from a serialized session payload dict."""
    provider = str(raw.get("provider", "unknown"))
    model_id_raw = raw.get("model_id")
    transport_raw = raw.get("transport")
    identity = MultimodalModelIdentity(
        provider=provider,
        model_id=str(model_id_raw) if model_id_raw is not None else None,
        transport=str(transport_raw) if transport_raw is not None else None,
    )
    raw_verdicts = raw.get("verdicts")
    if not isinstance(raw_verdicts, dict):
        return resolve_capability_profile(identity)
    verdicts: dict[str, CapabilityVerdict] = {}
    for modality, v in raw_verdicts.items():
        if not isinstance(v, dict):
            continue
        delivery_raw = v.get("delivery", "")
        try:
            delivery = DeliveryMode(str(delivery_raw))
        except ValueError:
            delivery = DeliveryMode.RESOURCE_REFERENCE_REPLAY
        block_type_raw = v.get("block_type")
        verdicts[modality] = CapabilityVerdict(
            modality=modality,
            delivery=delivery,
            provider=provider,
            model_id=str(model_id_raw) if model_id_raw is not None else None,
            reason=str(v.get("reason", "")),
            block_type=str(block_type_raw) if block_type_raw is not None else None,
        )
    for modality in SUPPORTED_MODALITIES:
        if modality not in verdicts:
            verdicts[modality] = get_delivery_mode(identity, modality)
    return ResolvedCapabilityProfile(identity=identity, verdicts=verdicts)


__all__ = [
    "UNKNOWN_IDENTITY",
    "CapabilityVerdict",
    "DeliveryMode",
    "MultimodalModelIdentity",
    "ResolvedCapabilityProfile",
    "get_delivery_mode",
    "profile_from_payload",
    "resolve_capability_profile",
]
