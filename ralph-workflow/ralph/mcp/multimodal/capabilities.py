
from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from ralph.mcp.multimodal._capability_verdict import CapabilityVerdict
from ralph.mcp.multimodal._delivery_mode import DeliveryMode
from ralph.mcp.multimodal._multimodal_model_identity import MultimodalModelIdentity
from ralph.mcp.multimodal.artifacts import SUPPORTED_MODALITIES
from ralph.mcp.multimodal.payload_fields import (
    payload_delivery,
    payload_identity,
    payload_model_id,
    payload_provider,
    payload_transport,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_TYPED_BLOCK_SUPPORT: dict[str, dict[str, str]] = {
    "claude": {"pdf": "pdf", "document": "document"},
    "anthropic": {"pdf": "pdf", "document": "document"},
    "gemini": {"pdf": "pdf", "document": "document", "audio": "audio", "video": "video"},
}


_MINTED_BLOCK_TYPES: frozenset[str] = frozenset(
    block_type for modalities in _TYPED_BLOCK_SUPPORT.values() for block_type in modalities.values()
)



_PROVIDER_UNSUPPORTED_MODALITIES: dict[str, frozenset[str]] = {
    "claude": frozenset({"audio", "video"}),
    "anthropic": frozenset({"audio", "video"}),
    "openai": frozenset({"audio", "video", "pdf", "document"}),
    "codex": frozenset({"audio", "video", "pdf", "document"}),
    "gemini": frozenset(),
}

_PROVIDER_UNSUPPORTED_REASON: dict[str, str] = {
    "claude": "Claude does not accept this modality via Ralph's managed MCP path",
    "anthropic": "Anthropic does not accept this modality via Ralph's managed MCP path",
    "openai": "OpenAI does not accept this modality via Ralph's managed MCP path",
    "codex": "Codex does not accept this modality via Ralph's managed MCP path",
}



_INLINE_IMAGE_ROUNDTRIP_UNSAFE_TRANSPORTS: frozenset[str] = frozenset({"codex"})

_INLINE_IMAGE_HANDLE_ONLY_PROVIDERS: frozenset[str] = frozenset({"ccs"})


def transport_inline_image_roundtrip_unsafe(transport: str | None) -> bool:
    return (transport or "").strip().lower() in _INLINE_IMAGE_ROUNDTRIP_UNSAFE_TRANSPORTS


def inline_image_roundtrip_unsafe(identity: MultimodalModelIdentity) -> bool:
    return transport_inline_image_roundtrip_unsafe(identity.transport)


def inline_image_requires_text_handle(identity: MultimodalModelIdentity) -> bool:
    return identity.provider.lower() in _INLINE_IMAGE_HANDLE_ONLY_PROVIDERS


def identity_on_transport(
    identity: MultimodalModelIdentity,
    transport: str | None,
) -> MultimodalModelIdentity:
    stated = (identity.transport or "").strip().lower()
    launched = (transport or "").strip().lower()
    if not stated and not launched:
        if identity.transport is not None:
            return replace(identity, transport=None)
        return identity
    if not launched or stated == launched:
        return identity if identity.transport == stated else replace(identity, transport=stated)
    if not stated:
        return replace(identity, transport=launched)
    return MultimodalModelIdentity(provider="unknown", model_id=None, transport=launched)


_PERCEPTIBLE_DELIVERIES: frozenset[DeliveryMode] = frozenset(
    {DeliveryMode.INLINE_IMAGE, DeliveryMode.TYPED_BLOCK}
)

_DELIVERY_DEMAND: dict[DeliveryMode, int] = {
    DeliveryMode.UNSUPPORTED: 0,
    DeliveryMode.RESOURCE_REFERENCE_REPLAY: 1,
    DeliveryMode.TYPED_BLOCK: 2,
    DeliveryMode.INLINE_IMAGE: 2,
}


def delivery_demand(delivery: DeliveryMode) -> int:
    return _DELIVERY_DEMAND[delivery]


def caller_identity_for(
    session_identity: MultimodalModelIdentity,
    delegated_identity: MultimodalModelIdentity | None,
) -> MultimodalModelIdentity:
    if delegated_identity is None:
        return session_identity
    return identity_on_transport(delegated_identity, session_identity.transport)


def caller_profile_for(
    session_identity: MultimodalModelIdentity,
    delegated_identity: MultimodalModelIdentity | None,
    session_profile: ResolvedCapabilityProfile | None,
    delegated_profile: ResolvedCapabilityProfile | None,
) -> ResolvedCapabilityProfile:
    stored = delegated_profile
    if stored is None and delegated_identity is None:
        stored = session_profile
    return profile_for_caller(stored, caller_identity_for(session_identity, delegated_identity))


def profile_for_caller(
    profile: ResolvedCapabilityProfile | None,
    identity: MultimodalModelIdentity,
) -> ResolvedCapabilityProfile:
    if profile is None:
        return resolve_capability_profile(identity)
    if profile.identity == identity:
        return profile
    if not (identity.transport or "").strip() and profile.identity.transport is not None:
        identity = replace(identity, transport=profile.identity.transport)
        if profile.identity == identity:
            return profile
    if (
        profile.identity.transport == identity.transport
        and profile.identity.provider == identity.provider
    ):
        return ResolvedCapabilityProfile(identity=identity, verdicts=profile.verdicts)
    return resolve_capability_profile(identity)


def select_session_transport(transports: Sequence[str]) -> str | None:
    if not transports:
        return None
    for transport in transports:
        if transport_inline_image_roundtrip_unsafe(transport):
            return transport
    first = transports[0]
    return first if all(t == first for t in transports) else None


_MIN_TRANSPORTS_TO_DISAGREE = 2


def session_transport_is_ambiguous(transports: Sequence[str]) -> bool:
    if len(transports) < _MIN_TRANSPORTS_TO_DISAGREE:
        return False
    for transport in transports:
        if transport_inline_image_roundtrip_unsafe(transport):
            return False
    first = transports[0]
    return any(transport != first for transport in transports)


TRANSPORT_FIXED_PROVIDER: Mapping[str, str] = MappingProxyType(
    {
        "claude": "claude",
        "claude_interactive": "claude",
        "codex": "openai",
        "agy": "gemini",
    }
)

VENDOR_ROUTING_TRANSPORTS: frozenset[str] = frozenset({"opencode"})


def cli_bound_provider(transport: str | None) -> str | None:
    if transport is None:
        return None
    fixed = TRANSPORT_FIXED_PROVIDER.get(transport)
    if fixed is not None:
        return fixed
    if transport in VENDOR_ROUTING_TRANSPORTS:
        return None
    return UNKNOWN_IDENTITY.provider


def get_delivery_mode(
    identity: MultimodalModelIdentity,
    modality: str,
) -> CapabilityVerdict:
    verdict = _delivery_for_provider(identity, modality)
    canonical = cli_bound_provider(identity.transport)
    if canonical is None or canonical == identity.provider:
        return verdict
    through_the_cli = _delivery_for_provider(replace(identity, provider=canonical), modality)
    if delivery_demand(through_the_cli.delivery) >= delivery_demand(verdict.delivery):
        return verdict
    unresolved = _delivery_for_provider(
        replace(identity, provider=UNKNOWN_IDENTITY.provider), modality
    )
    return replace(
        unresolved,
        provider=identity.provider,
        model_id=identity.model_id,
        reason=(
            f"{unresolved.reason} (Ralph cannot confirm the {canonical!r} CLI "
            f"carries {identity.provider!r} media, so delivery stays replayable)"
        ),
    )


def _delivery_for_provider(
    identity: MultimodalModelIdentity,
    modality: str,
) -> CapabilityVerdict:
    if modality not in SUPPORTED_MODALITIES:
        return CapabilityVerdict(
            modality=modality,
            delivery=DeliveryMode.UNSUPPORTED,
            provider=identity.provider,
            model_id=identity.model_id,
            reason=f"unknown modality '{modality}'",
        )

    if modality == "image":
        if inline_image_roundtrip_unsafe(identity):
            return CapabilityVerdict(
                modality=modality,
                delivery=DeliveryMode.RESOURCE_REFERENCE_REPLAY,
                provider=identity.provider,
                model_id=identity.model_id,
                reason=(
                    f"transport '{identity.transport}' cannot round-trip an inline "
                    "image block into its provider API request; delivering as "
                    "resource_reference_replay instead"
                ),
            )
        return CapabilityVerdict(
            modality=modality,
            delivery=DeliveryMode.INLINE_IMAGE,
            provider=identity.provider,
            model_id=identity.model_id,
            reason="image delivery does not guess model capability",
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

    typed_blocks = (
        {}
        if inline_image_roundtrip_unsafe(identity)
        else _TYPED_BLOCK_SUPPORT.get(provider_lower, {})
    )
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

    identity: MultimodalModelIdentity
    verdicts: dict[str, CapabilityVerdict]

    def verdict_for(self, modality: str) -> CapabilityVerdict:
        if modality not in self.verdicts:
            return get_delivery_mode(self.identity, modality)
        stored = self.verdicts[modality]
        fresh = get_delivery_mode(self.identity, modality)
        if delivery_demand(stored.delivery) > delivery_demand(fresh.delivery):
            return fresh
        if stored.delivery is not fresh.delivery and _PERCEPTIBLE_DELIVERIES.issuperset(
            {stored.delivery, fresh.delivery}
        ):
            return fresh
        if stored.delivery is DeliveryMode.TYPED_BLOCK and stored.block_type != fresh.block_type:
            return fresh
        return stored

    def to_payload(self) -> dict[str, object]:
        return {
            "provider": self.identity.provider,
            "model_id": self.identity.model_id,
            "transport": payload_transport(self.identity.transport),
            "verdicts": {
                modality: {
                    "delivery": corrected.delivery.value,
                    "reason": corrected.reason,
                    "block_type": corrected.block_type,
                }
                for modality, corrected in (
                    (modality, self.verdict_for(modality)) for modality in self.verdicts
                )
            },
        }


UNKNOWN_IDENTITY = MultimodalModelIdentity(provider="unknown")


def resolve_capability_profile(identity: MultimodalModelIdentity) -> ResolvedCapabilityProfile:
    canonical = _canonical_identity(identity)
    verdicts = {
        modality: get_delivery_mode(canonical, modality) for modality in SUPPORTED_MODALITIES
    }
    return ResolvedCapabilityProfile(identity=canonical, verdicts=verdicts)


def _canonical_identity(identity: MultimodalModelIdentity) -> MultimodalModelIdentity:
    canonical = payload_transport(identity.transport)
    if canonical == identity.transport:
        return identity
    return replace(identity, transport=canonical)


def payload_block_type(raw: object) -> str | None:
    return raw if isinstance(raw, str) and raw in _MINTED_BLOCK_TYPES else None


def _rehydrated_reason(
    identity: MultimodalModelIdentity,
    modality: str,
    delivery: DeliveryMode,
) -> str:
    fresh = get_delivery_mode(identity, modality)
    if delivery is fresh.delivery:
        return fresh.reason
    return (
        f"stored verdict '{delivery.value}' rehydrated from the session payload "
        f"for provider '{identity.provider}'; a fresh resolution says "
        f"'{fresh.delivery.value}'"
    )


def profile_from_payload(raw: dict[str, object]) -> ResolvedCapabilityProfile:
    identity = payload_identity(raw.get("provider"), raw.get("model_id"), raw.get("transport"))
    raw_verdicts = raw.get("verdicts")
    if not isinstance(raw_verdicts, dict):
        return resolve_capability_profile(identity)
    verdicts: dict[str, CapabilityVerdict] = {}
    for modality, v in raw_verdicts.items():
        if not isinstance(v, dict):
            continue
        delivery = payload_delivery(v.get("delivery"))
        verdicts[modality] = CapabilityVerdict(
            modality=modality,
            delivery=delivery,
            provider=identity.provider,
            model_id=identity.model_id,
            reason=_rehydrated_reason(identity, modality, delivery),
            block_type=payload_block_type(v.get("block_type")),
        )
    for modality in SUPPORTED_MODALITIES:
        if modality not in verdicts:
            verdicts[modality] = get_delivery_mode(identity, modality)
    return ResolvedCapabilityProfile(identity=identity, verdicts=verdicts)


__all__ = [
    "TRANSPORT_FIXED_PROVIDER",
    "UNKNOWN_IDENTITY",
    "VENDOR_ROUTING_TRANSPORTS",
    "CapabilityVerdict",
    "DeliveryMode",
    "MultimodalModelIdentity",
    "ResolvedCapabilityProfile",
    "caller_identity_for",
    "caller_profile_for",
    "cli_bound_provider",
    "delivery_demand",
    "get_delivery_mode",
    "identity_on_transport",
    "inline_image_requires_text_handle",
    "inline_image_roundtrip_unsafe",
    "payload_block_type",
    "payload_delivery",
    "payload_identity",
    "payload_model_id",
    "payload_provider",
    "payload_transport",
    "profile_for_caller",
    "profile_from_payload",
    "resolve_capability_profile",
    "select_session_transport",
    "session_transport_is_ambiguous",
    "transport_inline_image_roundtrip_unsafe",
]
