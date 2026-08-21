"""A transport bounds delivery downward, and only for a fixed-provider CLI.

Two claims the delivery rules rest on, neither previously checkable:

1. A stated transport can only ever RESTRICT what is delivered. Several
   rules read a transport and take the lesser of two answers; none may
   raise one. ``caller_identity_for`` depends on this -- it lets a
   delegate keep a transport it stated itself, which is only safe while
   a stated transport cannot buy capability.
2. The bound applies to the CLIs that call exactly ONE vendor's API and
   to no others. A router CLI reaches the named provider's own API, so
   its model's provider already IS the bound; adding one would withhold
   a typed block the target API accepts.

Both are swept across the whole matrix rather than sampled, because the
defect they guard is a transport or provider added to one table and not
the other.
"""

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
    """No (provider, modality) is delivered MORE readily for having a CLI."""
    raised: list[str] = []
    for provider in _PROVIDERS:
        for modality in SUPPORTED_MODALITIES:
            without = get_delivery_mode(
                MultimodalModelIdentity(provider=provider), modality
            )
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
    """Everything Ralph cannot name a vendor for is bounded, by default.

    ``resolve_model_identity`` answers ``nanocoder``, ``pi``, ``cursor``,
    ``kimi`` and ``generic`` with the transport's OWN name -- it never
    parses a vendor out of their model flags -- so Ralph has no basis
    for promising typed delivery through them. Only ``opencode`` reads a
    catalog and resolves a real vendor.

    An unrecognised transport is bounded too, so adding one is not a way
    to leave the guard behind.
    """
    unvouched = [
        transport
        for transport in _TRANSPORTS
        if transport not in TRANSPORT_FIXED_PROVIDER
        and transport not in VENDOR_ROUTING_TRANSPORTS
    ]
    assert set(unvouched) == {"nanocoder", "generic", "pi", "cursor", "kimi"}
    for transport in unvouched:
        for modality in SUPPORTED_MODALITIES:
            claimed = MultimodalModelIdentity(
                provider="gemini", model_id="g", transport=transport
            )
            delivery = get_delivery_mode(claimed, modality).delivery
            assert delivery is not DeliveryMode.TYPED_BLOCK, f"{transport}/{modality}"
    unknown_cli = MultimodalModelIdentity(provider="gemini", transport="a-cli-added-later")
    assert (
        get_delivery_mode(unknown_cli, "audio").delivery is not DeliveryMode.TYPED_BLOCK
    )


def test_the_bound_costs_nothing_ralph_resolves_for_itself() -> None:
    """Bounding those CLIs withholds nothing from an ordinary run.

    Their resolved providers are the transport names, which carry no
    typed blocks in the matrix -- so the two answers agree and the
    verdict stands. What the bound closes is the provider a PAYLOAD
    names: a stale or hand-written ``model_identity`` claiming ``gemini``
    on a ``kimi`` session minted an AudioContent through a CLI Ralph
    knows nothing about.
    """
    for transport in AgentTransport:
        resolved = resolve_model_identity(transport, None)
        for modality in SUPPORTED_MODALITIES:
            bounded = get_delivery_mode(resolved, modality)
            unbounded = get_delivery_mode(
                MultimodalModelIdentity(
                    provider=resolved.provider, model_id=resolved.model_id
                ),
                modality,
            )
            if transport_inline_image_roundtrip_unsafe(transport.value):
                continue
            assert bounded.delivery == unbounded.delivery, (
                f"{transport.value}/{modality}: bound changed a resolved identity"
            )


def test_a_vendor_routing_cli_still_delivers_its_model_s_typed_blocks() -> None:
    """``opencode`` reaches the API its model names, so it is not bounded.

    This is the file's one unmeasured assumption -- nothing in the repo
    records what the opencode CLI does with a typed block. It is pinned
    here so that changing it is a decision rather than a drift.
    """
    assert frozenset({"opencode"}) == VENDOR_ROUTING_TRANSPORTS
    routed = MultimodalModelIdentity(provider="anthropic", transport="opencode")
    assert get_delivery_mode(routed, "pdf").delivery is DeliveryMode.TYPED_BLOCK


def test_the_two_fixed_provider_tables_are_one_table() -> None:
    """Identity resolution and delivery agree on which CLIs are fixed.

    Kept as two hand-written literals, either could gain a transport the
    other did not -- resolving an identity the delivery side then
    declines to bound, which is exactly the gap the bound was added to
    close. The membership is pinned OUTRIGHT rather than derived from
    the behaviour it drives: a dropped entry makes both sides agree that
    the CLI is a router, so nothing behavioural contradicts it and the
    only thing that can is a statement of what the table is for.

    - ``claude`` / ``claude_interactive``: the CLI calls Anthropic's API
      whatever ``--model`` says.
    - ``codex``: the CLI calls OpenAI's Responses API.
    - ``agy``: the CLI calls Gemini's API.

    Anything else routes to the provider its model names, and belongs in
    neither table.
    """
    assert set(TRANSPORT_FIXED_PROVIDER) == {"claude", "claude_interactive", "codex", "agy"}
    for transport in AgentTransport:
        expected = TRANSPORT_FIXED_PROVIDER.get(transport.value)
        if expected is None:
            continue
        assert resolve_model_identity(transport, None).provider == expected, transport.value


#: The bound announces itself in the verdict's reason. Comparing
#: DELIVERIES cannot tell it apart from the round-trip-unsafe
#: suppression, which reaches the same answer by a different route for
#: ``codex``; the reason is what distinguishes which guard spoke.
_BOUND_MARKER = "Ralph cannot confirm the"


def test_each_fixed_provider_entry_is_load_bearing_somewhere() -> None:
    """Every entry changes an answer -- for one of them, not a delivery one.

    ``claude`` and ``claude_interactive`` bound delivery outright:
    dropping either hands a ``--model gemini/...`` session the
    AudioContent the claude CLI cannot take, and ``claude_interactive``
    is the entry that survived every mutation round with nothing testing
    it. ``codex`` fires the bound too, though it changes no delivery:
    the round-trip-unsafe suppression has already reduced the answer, so
    the bound only re-states it against an unresolved provider.

    ``agy`` fires nothing and cannot -- its vendor is ``gemini``, the
    most capable row in the matrix, so the lesser of the two answers is
    never the CLI's. It is not dead weight: since the two tables became
    one it decides what ``resolve_model_identity`` resolves, which the
    test above pins. Stated so a later reader does not delete it.
    """
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
    """The measured wire failure covers every modality, not just images.

    Redundant TODAY with the fixed-provider bound, because the only
    round-trip-unsafe transport (``codex``) is also a fixed-provider one
    whose vendor declines those modalities anyway. It stops being
    redundant the moment either set changes, which is what this sweep is
    for: removing the suppression alone left the suite green.
    """
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
