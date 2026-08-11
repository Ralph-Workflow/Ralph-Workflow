"""Multimodal capability detection and delivery policy.

This module is the single source of truth for provider/model identity,
capability detection, and delivery policy decisions. All runtime layers that
need to determine whether a modality can be delivered must derive their answer
from this module rather than re-declaring provider knowledge elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.api.opencode import get_model_by_id
from ralph.mcp.multimodal._capability_verdict import CapabilityVerdict
from ralph.mcp.multimodal._delivery_mode import DeliveryMode
from ralph.mcp.multimodal._multimodal_model_identity import MultimodalModelIdentity
from ralph.mcp.multimodal.artifacts import SUPPORTED_MODALITIES

if TYPE_CHECKING:
    from collections.abc import Callable

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


#: Per-provider inline-image gate predicates. The default callable for
#: every provider (including those that ship all-models-vision) is the
#: constant ``True``. ``openai`` consults the model's identifier so a
#: non-vision model (e.g. ``gpt-3.5-turbo``) is not falsely granted
#: inline-image delivery.
_INLINE_IMAGE_GATE_BY_PROVIDER: dict[str, Callable[[str | None], bool]] = {
    "claude": _claude_supports_inline_image,
    "anthropic": _claude_supports_inline_image,
    "openai": _openai_supports_inline_image,
    "codex": _openai_supports_inline_image,
    "gemini": _gemini_supports_inline_image,
}


def _inline_image_reason(provider: str, model_id: str | None) -> str | None:
    """Return a human-readable reason string if the provider supports inline images."""
    gate = _INLINE_IMAGE_GATE_BY_PROVIDER.get(provider)
    if gate is not None and gate(model_id):
        return f"{provider} supports inline image delivery"
    return None


def _opencode_supports_inline_image_via_catalog(model_id: str | None) -> bool:
    """Grant inline-image when the OpenCode catalog reports ``image`` in modalities.input.

    The catalog is the source of truth for OpenCode catalog-backed
    models: a model whose ``modalities.input`` contains ``"image"``
    accepts inline image data. Models absent from the catalog, or
    catalog entries whose ``modalities.input`` lacks ``"image"``,
    fall through to the resource_reference_replay delivery.
    """
    if model_id is None:
        return False
    try:
        entry = get_model_by_id(model_id)
    except Exception:
        return False
    if entry is None:
        return False
    return "image" in entry.modalities_input


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
        if inline_reason is not None:
            return CapabilityVerdict(
                modality=modality,
                delivery=DeliveryMode.INLINE_IMAGE,
                provider=identity.provider,
                model_id=identity.model_id,
                reason=inline_reason,
            )
        if provider_lower == "opencode" and _opencode_supports_inline_image_via_catalog(
            identity.model_id
        ):
            return CapabilityVerdict(
                modality=modality,
                delivery=DeliveryMode.INLINE_IMAGE,
                provider=identity.provider,
                model_id=identity.model_id,
                reason=(
                    f"opencode catalog reports 'image' in modalities.input for "
                    f"model '{identity.model_id}'"
                ),
            )
        return CapabilityVerdict(
            modality=modality,
            delivery=DeliveryMode.RESOURCE_REFERENCE_REPLAY,
            provider=identity.provider,
            model_id=identity.model_id,
            reason=f"provider '{identity.provider}' does not support inline image delivery",
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
