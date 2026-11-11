from __future__ import annotations

from ralph.config.enums import AgentTransport
from ralph.mcp.multimodal.artifacts import SUPPORTED_MODALITIES
from ralph.mcp.multimodal.capabilities import (
    TRANSPORT_FIXED_PROVIDER,
    VENDOR_ROUTING_TRANSPORTS,
    DeliveryMode,
    MultimodalModelIdentity,
    delivery_demand,
    get_delivery_mode,
    transport_inline_image_roundtrip_unsafe,
)
from ralph.mcp.session_plan import resolve_model_identity

_PROVIDERS = (
    "claude",
    "anthropic",
    "openai",
    "codex",
    "gemini",
    "ccs",
    "unknown",
    "a-provider-ralph-has-never-heard-of",
)
_TRANSPORTS = tuple(transport.value for transport in AgentTransport)


def test_a_stated_transport_can_only_restrict_delivery() -> None:
    raised: list[str] = []
    for provider in _PROVIDERS:
        for modality in SUPPORTED_MODALITIES:
            without = get_delivery_mode(MultimodalModelIdentity(provider=provider), modality)
            for transport in _TRANSPORTS:
                on_cli = get_delivery_mode(
                    MultimodalModelIdentity(provider=provider, transport=transport),
                    modality,
                )
                if delivery_demand(on_cli.delivery) > delivery_demand(without.delivery):
                    raised.append(
                        f"{provider}/{modality} on {transport}: "
                        f"{without.delivery.value} -> {on_cli.delivery.value}"
                    )
    assert raised == []


def test_only_a_vendor_routing_cli_escapes_the_bound() -> None:
    unvouched = [
        transport
        for transport in _TRANSPORTS
        if transport not in TRANSPORT_FIXED_PROVIDER and transport not in VENDOR_ROUTING_TRANSPORTS
    ]
    assert set(unvouched) == {"nanocoder", "generic", "pi", "cursor", "kimi"}
    for transport in unvouched:
        for modality in SUPPORTED_MODALITIES:
            claimed = MultimodalModelIdentity(provider="gemini", model_id="g", transport=transport)
            delivery = get_delivery_mode(claimed, modality).delivery
            assert delivery is not DeliveryMode.TYPED_BLOCK, f"{transport}/{modality}"
    unknown_cli = MultimodalModelIdentity(provider="gemini", transport="a-cli-added-later")
    assert get_delivery_mode(unknown_cli, "audio").delivery is not DeliveryMode.TYPED_BLOCK


def test_the_bound_costs_nothing_ralph_resolves_for_itself() -> None:
    for transport in AgentTransport:
        resolved = resolve_model_identity(transport, None)
        for modality in SUPPORTED_MODALITIES:
            bounded = get_delivery_mode(resolved, modality)
            unbounded = get_delivery_mode(
                MultimodalModelIdentity(provider=resolved.provider, model_id=resolved.model_id),
                modality,
            )
            if transport_inline_image_roundtrip_unsafe(transport.value):
                continue
            assert bounded.delivery == unbounded.delivery, (
                f"{transport.value}/{modality}: bound changed a resolved identity"
            )


def test_a_vendor_routing_cli_still_delivers_its_model_s_typed_blocks() -> None:
    assert frozenset({"opencode"}) == VENDOR_ROUTING_TRANSPORTS
    routed = MultimodalModelIdentity(provider="anthropic", transport="opencode")
    assert get_delivery_mode(routed, "pdf").delivery is DeliveryMode.TYPED_BLOCK


def test_the_two_fixed_provider_tables_are_one_table() -> None:
    assert set(TRANSPORT_FIXED_PROVIDER) == {"claude", "claude_interactive", "codex", "agy"}
    for transport in AgentTransport:
        expected = TRANSPORT_FIXED_PROVIDER.get(transport.value)
        if expected is None:
            continue
        assert resolve_model_identity(transport, None).provider == expected, transport.value


_BOUND_MARKER = "Ralph cannot confirm the"


def test_each_fixed_provider_entry_is_load_bearing_somewhere() -> None:
    fires_the_bound = {
        transport
        for transport in TRANSPORT_FIXED_PROVIDER
        for modality in SUPPORTED_MODALITIES
        if _BOUND_MARKER
        in get_delivery_mode(
            MultimodalModelIdentity(provider="gemini", transport=transport), modality
        ).reason
    }
    assert fires_the_bound == {"claude", "claude_interactive", "codex"}
    assert all(
        _BOUND_MARKER
        not in get_delivery_mode(
            MultimodalModelIdentity(provider="gemini", transport="agy"), modality
        ).reason
        for modality in SUPPORTED_MODALITIES
    )


def test_a_round_trip_unsafe_transport_never_gets_a_typed_block() -> None:
    unsafe = [t for t in _TRANSPORTS if transport_inline_image_roundtrip_unsafe(t)]
    assert unsafe, "no transport is marked round-trip-unsafe"
    for transport in unsafe:
        for provider in _PROVIDERS:
            for modality in SUPPORTED_MODALITIES:
                verdict = get_delivery_mode(
                    MultimodalModelIdentity(provider=provider, transport=transport),
                    modality,
                )
                assert verdict.delivery is not DeliveryMode.TYPED_BLOCK, (
                    f"{provider}/{modality} on {transport}"
                )
                assert verdict.delivery is not DeliveryMode.INLINE_IMAGE, (
                    f"{provider}/{modality} on {transport}"
                )
