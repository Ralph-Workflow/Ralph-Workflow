"""Multimodal capability detection and delivery policy.

This module is the single source of truth for provider/model identity,
capability detection, and delivery policy decisions. All runtime layers that
need to determine whether a modality can be delivered must derive their answer
from this module rather than re-declaring provider knowledge elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from ralph.mcp.multimodal._capability_verdict import CapabilityVerdict
from ralph.mcp.multimodal._delivery_mode import DeliveryMode
from ralph.mcp.multimodal._multimodal_model_identity import MultimodalModelIdentity
from ralph.mcp.multimodal.artifacts import SUPPORTED_MODALITIES

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

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


# ---------------------------------------------------------------------------
# Inline-image round-trip safety (transport-keyed, not provider-keyed)
# ---------------------------------------------------------------------------

#: Agent transports whose CLI cannot round-trip an inline MCP image block
#: back into its own provider API request.
#:
#: Measured 2026-08-20 on ``codex-cli 0.147.0``: after a ``read_media``
#: call answered with a base64 ``ImageContent`` block, the CLI
#: re-serialised that tool result into its next Responses API request
#: using the content-part type ``output_text``. The API rejects it::
#:
#:     [400]: Invalid value: 'output_text'. Supported values are:
#:     'input_text', 'input_image', 'input_file', and 'scoped_content'.
#:     (param: input[101].output[1])
#:
#: The turn died (``turn.failed``) and the whole work unit was graded
#: ``FAILED (no artifact)``. The defect is in the CLI's wire
#: serialisation, so Ralph cannot repair it — it can only decline to
#: hand that transport an inline image. Delivery degrades to
#: ``RESOURCE_REFERENCE_REPLAY``, which keeps the artifact registered
#: and replayable instead of killing the turn.
#:
#: This is keyed on TRANSPORT, not provider, for two reasons: the bug
#: lives in the CLI rather than in any provider's API (so a Codex CLI
#: pointed at ``--model anthropic/claude-...`` is equally affected),
#: and a Codex CLI run resolves to ``provider='openai'`` anyway (see
#: ``_TRANSPORT_FIXED_PROVIDER`` in ``ralph.mcp.session_plan``), so a
#: provider-keyed check would never fire.
#:
#: Remove the entry once codex-cli serialises MCP image results with an
#: ``input_*`` content-part type; the regression suite in
#: ``tests/test_codex_inline_image_roundtrip.py`` pins the contract.
_INLINE_IMAGE_ROUNDTRIP_UNSAFE_TRANSPORTS: frozenset[str] = frozenset({"codex"})

#: Providers that accept the standard MCP image union but still require a
#: Ralph-minted text handle for the mandatory replay hop.
_INLINE_IMAGE_HANDLE_ONLY_PROVIDERS: frozenset[str] = frozenset({"ccs"})


def transport_inline_image_roundtrip_unsafe(transport: str | None) -> bool:
    """Return True when ``transport`` cannot carry an inline image block.

    The transport-only form of :func:`inline_image_roundtrip_unsafe`, for
    callers that hold a transport but no resolved identity yet (session
    construction, chain planning).
    """
    return (transport or "").strip().lower() in _INLINE_IMAGE_ROUNDTRIP_UNSAFE_TRANSPORTS


def inline_image_roundtrip_unsafe(identity: MultimodalModelIdentity) -> bool:
    """Return True when ``identity``'s transport cannot take an inline image.

    See :data:`_INLINE_IMAGE_ROUNDTRIP_UNSAFE_TRANSPORTS` for the
    measured failure this guards. Callers on the media-delivery path
    MUST consult this (directly, or via the ``RESOURCE_REFERENCE_REPLAY``
    verdict :func:`get_delivery_mode` returns for such identities)
    before emitting an ``ImageContent`` block.
    """
    return transport_inline_image_roundtrip_unsafe(identity.transport)


def inline_image_requires_text_handle(identity: MultimodalModelIdentity) -> bool:
    """Return True when inline image bytes must be replaced by a text handle.

    Distinct from :func:`inline_image_roundtrip_unsafe`: these providers
    *can* carry the image union but need the Ralph-minted handle for the
    replay hop, so the verdict stays ``INLINE_IMAGE`` and only the
    emitted block changes.
    """
    return identity.provider.lower() in _INLINE_IMAGE_HANDLE_ONLY_PROVIDERS


def identity_on_transport(
    identity: MultimodalModelIdentity,
    transport: str | None,
) -> MultimodalModelIdentity:
    """Re-base ``identity`` onto the CLI actually on the other end.

    The transport describes the PROCESS, and only one process is ever on
    the other end of a session. A stated transport that disagrees with it
    is stale, so it is replaced -- and the provider beside it is dropped,
    because it described that other CLI: keeping it mints a typed PDF
    block for a CLI that cannot take one, or a hard unsupported error for
    one that can. Same reasoning either way round.

    ``transport`` of ``None`` means the caller knows no better, so the
    identity is returned untouched.
    """
    # Normalise both sides first. A whitespace-only transport is not a
    # transport: treating one as authoritative let ``--agent-transport
    # "  "`` override a delegate's genuine restricted CLI and then strip
    # to nothing, reopening the incident at the one seam the payload
    # readers' normalisation did not cover. Comparing raw also made a
    # difference in case alone look like a different CLI and cost the
    # identity its provider and model id.
    # Canonicalised, not merely compared. Every consumer strips and
    # lowercases before matching, so a difference in spelling changed
    # nothing about behaviour -- but the spelling IS written into the
    # session file and the wire-ledger capability digest, and the other
    # seam that re-bases a transport (``_reconcile_injected_transport``)
    # lowercases. Two seams normalising differently made the digest
    # depend on which one ran, and left this function returning
    # ``transport='CODEX'`` while claiming to have re-based the identity
    # onto the launched ``codex``.
    stated = (identity.transport or "").strip().lower()
    launched = (transport or "").strip().lower()
    if not stated and not launched:
        if identity.transport is not None:
            # A BLANK transport is not an unknown one. Left untouched it
            # kept ``transport=""``, which ``identity_is_serialisable``
            # reads as known -- so ``"transport": ""`` was written into
            # the child payload and the capability digest, and only the
            # reader's normalisation dropped it again. Unknown means
            # ``None`` at every seam.
            return replace(identity, transport=None)
        # Neither side knows the CLI. Returned UNTOUCHED, as documented:
        # canonicalising here rewrote a ``None`` transport to ``""``,
        # which reads as "known to be nothing" and flips
        # ``lifecycle.identity_is_serialisable`` (it tests
        # ``transport is not None``) -- so an identity nothing knew
        # anything about started being written to the session file.
        return identity
    if not launched or stated == launched:
        return identity if identity.transport == stated else replace(identity, transport=stated)
    if not stated:
        return replace(identity, transport=launched)
    return MultimodalModelIdentity(provider="unknown", model_id=None, transport=launched)


#: Deliveries that actually put the artifact in front of the model.
#: A stored verdict promising one of these when the identity does not is
#: the shape that hands a restricted CLI a block it cannot carry.
_PERCEPTIBLE_DELIVERIES: frozenset[DeliveryMode] = frozenset(
    {DeliveryMode.INLINE_IMAGE, DeliveryMode.TYPED_BLOCK}
)

#: How much each delivery asks of the receiving CLI, ordered.
#:
#: Splitting deliveries into "perceptible" and "everything else" made
#: ``RESOURCE_REFERENCE_REPLAY`` indistinguishable from ``UNSUPPORTED``,
#: so a stored reference-replay verdict survived against an identity
#: whose fresh answer was ``UNSUPPORTED`` -- delivering a resource
#: reference where a fresh profile returns an error. A reference is
#: strictly MORE than nothing, so it needs its own rung. The two
#: perceptible modes share a rung: neither is more demanding than the
#: other, they simply differ in shape.
_DELIVERY_DEMAND: dict[DeliveryMode, int] = {
    DeliveryMode.UNSUPPORTED: 0,
    DeliveryMode.RESOURCE_REFERENCE_REPLAY: 1,
    DeliveryMode.TYPED_BLOCK: 2,
    DeliveryMode.INLINE_IMAGE: 2,
}


def delivery_demand(delivery: DeliveryMode) -> int:
    """Return how much ``delivery`` asks of the receiving CLI.

    THE reading of :data:`_DELIVERY_DEMAND`, used by the guards
    themselves and not only by the tests that describe them. Added as a
    test-facing accessor while production kept indexing the private dict
    directly, it made the sweep that swept every provider and transport
    vacuous: stubbing it to ``return 0`` turned the comparison into
    ``0 > 0`` and the test could no longer fail, while every guard went
    on working. An accessor nothing production calls does not describe
    production.

    Every rule that combines two answers about one artifact -- the
    CLI bound, the stored-verdict correction -- takes the LESSER of the
    two by this order.
    """
    return _DELIVERY_DEMAND[delivery]


def caller_identity_for(
    session_identity: MultimodalModelIdentity,
    delegated_identity: MultimodalModelIdentity | None,
) -> MultimodalModelIdentity:
    """Return the identity a delegated call must be judged against.

    A delegate names a different MODEL, never a different CLI: the
    transport describes the process on the other end of this session, so
    the session's transport WINS rather than merely filling a blank.
    Letting a delegate state its own transport was a way to declare the
    guards away.

    "Wins" is exact only where the session HAS a transport;
    ``identity_on_transport`` returns the identity untouched when it does
    not, so a delegate that states its own CLI keeps it on a session
    that knows nothing about the process it is talking to. That is not a
    way to declare the guards away, and dropping it would be the unsafe
    change: a stated transport can only ever RESTRICT delivery -- it is
    read by the round-trip-unsafe check and by the fixed-provider bound,
    both of which take the lesser answer, and by nothing that raises one
    (pinned by ``test_a_stated_transport_can_only_restrict_delivery``).
    A delegate that names a capable CLI on a blank session gains
    whatever its own PROVIDER already granted it, and no more.

    THE one definition. Three copies of this rule existed -- in
    ``AgentSession``, in ``FileBackedSession``, and in the test double
    every media test runs through -- so a mutation to any single copy
    left the others green, and the double could drift from the pair it
    was standing in for without a failure anywhere.
    """
    if delegated_identity is None:
        return session_identity
    return identity_on_transport(delegated_identity, session_identity.transport)


def caller_profile_for(
    session_identity: MultimodalModelIdentity,
    delegated_identity: MultimodalModelIdentity | None,
    session_profile: ResolvedCapabilityProfile | None,
    delegated_profile: ResolvedCapabilityProfile | None,
) -> ResolvedCapabilityProfile:
    """Return the capability profile a delegated call must be judged against.

    A delegate that names its own model does NOT inherit the parent's
    profile -- that profile answers for a different model -- so the
    stored profile is dropped and re-resolved. See
    :func:`caller_identity_for` for why this lives in one place.
    """
    stored = delegated_profile
    if stored is None and delegated_identity is None:
        stored = session_profile
    return profile_for_caller(
        stored, caller_identity_for(session_identity, delegated_identity)
    )


def profile_for_caller(
    profile: ResolvedCapabilityProfile | None,
    identity: MultimodalModelIdentity,
) -> ResolvedCapabilityProfile:
    """Return ``profile`` re-based on the caller's authoritative identity.

    A session carries two notions of who it is talking to: the resolved
    identity, and the identity embedded in a stored capability profile.
    They can disagree -- a profile serialised by a parent that did not
    know the agent CLI, next to an identity an operator declared -- and
    the delivery guards read the PROFILE'S. Correcting only the identity
    therefore closed nothing, which is exactly how a declared transport
    kept failing to reach the media surface.

    Re-basing here makes the divergence unrepresentable: the profile a
    caller sees always carries the identity the session resolved, and
    its verdicts are re-derived when that identity has moved on.
    """
    if profile is None:
        return resolve_capability_profile(identity)
    if profile.identity == identity:
        return profile
    if not (identity.transport or "").strip() and profile.identity.transport is not None:
        # The profile knows which CLI this is and the identity does not.
        # Re-resolving here would answer a strictly less-informed
        # question and could relax a restricted profile.
        identity = replace(identity, transport=profile.identity.transport)
        if profile.identity == identity:
            return profile
    if (
        profile.identity.transport == identity.transport
        and profile.identity.provider == identity.provider
    ):
        # Same CLI and same provider -- only the model id differs, which
        # no verdict keys on. Keep the stored verdicts and carry the
        # authoritative identity.
        return ResolvedCapabilityProfile(identity=identity, verdicts=profile.verdicts)
    # A different provider's verdicts describe a different API. Keeping
    # them promised typed PDF blocks a provider does not accept, and left
    # each verdict's own ``provider`` and ``reason`` naming the old one
    # -- text this layer prints, and the wire ledger records.
    return resolve_capability_profile(identity)


def select_session_transport(transports: Sequence[str]) -> str | None:
    """Pick the transport to tag a session serving several candidate agents.

    A session is built before anyone knows which agent in a chain will
    actually run, so a mixed chain has no single correct answer. Resolve
    it conservatively: if ANY candidate is one the delivery guards must
    restrict, tag the session with that, because degrading a capable
    agent to a resource reference is harmless while handing a restricted
    agent an inline image kills its turn.

    A homogeneous chain uses its own transport. A mixed chain of
    unrestricted agents has no honest answer, so it gets ``None``.
    """
    if not transports:
        return None
    for transport in transports:
        if transport_inline_image_roundtrip_unsafe(transport):
            return transport
    first = transports[0]
    return first if all(t == first for t in transports) else None


#: Two candidates are the fewest that can name different CLIs.
_MIN_TRANSPORTS_TO_DISAGREE = 2


def session_transport_is_ambiguous(transports: Sequence[str]) -> bool:
    """True when candidates name different CLIs and none is restricted.

    :func:`select_session_transport` answers ``None`` both when there is
    nothing to go on (no candidates) and when the candidates genuinely
    DISAGREE. Those need different handling downstream, and collapsing
    them let a phase session keep the first agent's model flag for a
    mixed chain: the session then resolved that agent's PROVIDER and
    minted typed blocks -- ``PdfContent`` for a chain whose fallback is
    an opencode CLI, ``AudioContent``/``VideoContent`` for one whose
    fallback is claude -- none of which the agent that actually ran can
    carry. Restricting only the round-trip-unsafe case left this
    order-dependent: the same chain behaved differently depending on
    which agent was listed first.
    """
    if len(transports) < _MIN_TRANSPORTS_TO_DISAGREE:
        return False
    for transport in transports:
        if transport_inline_image_roundtrip_unsafe(transport):
            return False
    first = transports[0]
    return any(transport != first for transport in transports)


#: The provider a transport is FIXED to: the CLIs that call exactly ONE
#: vendor's API, whatever model flag they are given.
#:
#: MEMBERSHIP IS THE WHOLE RULE, and it is not "CLIs Ralph happens to
#: know about". A fixed-provider CLI re-serialises whatever a tool
#: returns into that one vendor's request, so a model flag naming
#: another vendor changes the MODEL and not the API the block has to
#: survive. A router CLI -- ``opencode``, ``cursor``, ``kimi``, ``pi``,
#: ``generic``, ``nanocoder`` -- reaches the named provider's own API,
#: so its model's provider IS the bound and there is no second answer to
#: take the lesser of. Bounding those by a CLI vendor would withhold a
#: typed block the target API accepts.
#:
#: THE definition. ``session_plan`` derives its enum-keyed view from
#: this dict rather than restating it: the two answer the same question
#: -- one resolves an identity, the other decides what that identity may
#: be DELIVERED -- and a "mirrors X" comment is not a mechanism.
TRANSPORT_FIXED_PROVIDER: Mapping[str, str] = MappingProxyType(
    {
        "claude": "claude",
        "claude_interactive": "claude",
        "codex": "openai",
        "agy": "gemini",
    }
)

#: The CLIs that resolve a real VENDOR from the model they are given,
#: and are therefore bounded by that vendor and by nothing else.
#:
#: Exactly one today. ``opencode`` reads a catalog and answers
#: ``(anthropic, opencode)`` for ``--model anthropic/...``, so the API
#: the block must survive is anthropic's and a CLI-shaped bound would
#: withhold delivery the target accepts.
#:
#: This is an ASSUMPTION, not a measurement, and it is the only
#: unmeasured one left in this file: nothing in this repo records what
#: the opencode CLI does with a typed block, unlike the codex bound
#: above, which carries a dated wire capture. It is stated here so the
#: next person can measure it rather than inherit it as settled.
VENDOR_ROUTING_TRANSPORTS: frozenset[str] = frozenset({"opencode"})


def cli_bound_provider(transport: str | None) -> str | None:
    """Return the provider whose acceptance bounds delivery through a CLI.

    Three answers, and the DEFAULT is the conservative one:

    * A fixed-provider CLI is bounded by its vendor. It calls one
      vendor's API whatever ``--model`` says, so a foreign model flag
      changes the model and not the request format the block must
      survive.
    * A vendor-routing CLI is bounded by nothing here: it reaches the
      API its model names, which ``_delivery_for_provider`` has already
      answered for.
    * EVERYTHING ELSE is bounded by an unresolved provider. Ralph has no
      vendor for ``nanocoder``, ``pi``, ``cursor``, ``kimi`` or
      ``generic`` -- ``resolve_model_identity`` answers them with the
      transport's own name -- so it has no basis for promising typed
      delivery through them and says so instead of guessing.

    That default costs nothing Ralph resolves for itself: none of those
    transports' provider slugs appears in ``_TYPED_BLOCK_SUPPORT``, so
    the two answers agree and the verdict stands. What it closes is the
    provider a PAYLOAD names -- a hand-written or stale
    ``model_identity`` claiming ``gemini`` on a ``kimi`` session, which
    minted an AudioContent through a CLI Ralph knows nothing about. It
    also makes an unrecognised transport safe by default, so adding one
    is no longer a way to leave the guard behind.

    ``generic`` matters most: ``multimodal_status`` declares that
    transport as carrying NO MCP at all, and it was still handed typed
    blocks.
    """
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
    """Determine how to deliver a modality for the given model identity.

    Bounded by the model's provider and, for a FIXED-PROVIDER CLI, by
    that CLI's own vendor as well. A model flag naming a qualified
    ``provider/model`` deliberately overrides the transport's canonical
    provider, but on a fixed-provider CLI the block still has to survive
    that one vendor's request format. Taking only the model's provider
    let a ``claude``-transport agent carrying ``--model gemini/...`` mint
    AudioContent and VideoContent, which Ralph's own matrix says the
    claude CLI does not accept. Neither side may be exceeded, so the
    LESS demanding of the two answers wins -- the same conservative rule
    a mixed chain and a declared-vs-persisted transport already use.

    A ROUTER CLI has no such second answer and gets no such bound: it
    reaches the named provider's own API, so its model's provider
    already IS the API the block must survive. See
    :data:`TRANSPORT_FIXED_PROVIDER` for why that membership is the
    whole rule.

    Returns a CapabilityVerdict indicating the delivery mode:

    - INLINE_IMAGE: provider accepts inline base64 image data.
    - TYPED_BLOCK: provider accepts a named typed block (pdf, document, audio, video).
    - RESOURCE_REFERENCE_REPLAY: unknown provider; multimodal surface stays visible
      via resource reference replay handle.
    - UNSUPPORTED: provider cannot accept this modality via Ralph's managed path.

    Unknown providers default to RESOURCE_REFERENCE_REPLAY (safe, keeps multimodal
    surface available without false typed-delivery promises).
    """
    verdict = _delivery_for_provider(identity, modality)
    canonical = cli_bound_provider(identity.transport)
    if canonical is None or canonical == identity.provider:
        return verdict
    through_the_cli = _delivery_for_provider(replace(identity, provider=canonical), modality)
    if delivery_demand(through_the_cli.delivery) >= delivery_demand(verdict.delivery):
        return verdict
    # Resolved as an UNRESOLVED provider, not as the CLI's own verdict.
    # That is what this pairing actually is -- Ralph cannot confirm what
    # a fixed-provider CLI carries for a model from another vendor --
    # and it lands on a replay handle rather than the CLI's
    # ``unsupported``. Adopting the CLI's answer wholesale would turn a
    # reachable artifact into a hard error, which is a bigger loss than
    # the typed block it withholds: a reference is strictly more than
    # nothing.
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
    """Resolve the verdict for this identity's PROVIDER alone."""
    if modality not in SUPPORTED_MODALITIES:
        return CapabilityVerdict(
            modality=modality,
            delivery=DeliveryMode.UNSUPPORTED,
            provider=identity.provider,
            model_id=identity.model_id,
            reason=f"unknown modality '{modality}'",
        )

    if modality == "image":
        # Criterion 14 ("unresolvable -> capable") makes inline the
        # default for images regardless of how the identity resolved --
        # EXCEPT for a transport whose CLI provably cannot round-trip the
        # block back into its own API request. That is a measured wire
        # failure, not a capability guess, so it is not covered by the
        # "do not guess" rule above.
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
    #
    # A round-trip-unsafe transport gets NO Ralph-minted typed block
    # either. The measured failure is the CLI re-serialising a
    # non-standard content block into its own API request, and a
    # ``pdf`` / ``document`` block is the same construct as the image
    # one -- the modality differs, the hazard does not. This is an
    # inference from the measured image case rather than a second
    # measurement, and it is the conservative direction: the artifact
    # still arrives as a resource reference.
    typed_blocks = (
        {} if inline_image_roundtrip_unsafe(identity) else _TYPED_BLOCK_SUPPORT.get(provider_lower, {})
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
    """Pre-computed capability verdicts for a resolved model identity.

    This is the runtime-owned contract for multimodal delivery decisions.
    Downstream layers consume this profile from the session rather than
    re-calling get_delivery_mode() at each use site.
    """

    identity: MultimodalModelIdentity
    verdicts: dict[str, CapabilityVerdict]

    def verdict_for(self, modality: str) -> CapabilityVerdict:
        """Return the verdict for ``modality``, corrected against the identity.

        A STORED verdict is untrusted input. ``profile_from_payload``
        takes a persisted ``delivery`` and ``block_type`` verbatim, so a
        session written before a guard existed -- or by a different
        transport, or by hand -- can name a delivery this identity must
        not be given. Correcting only ``inline_image`` left the same hole
        one modality over: a stored ``pdf -> typed_block`` handed a
        Ralph-minted typed block to a CLI that cannot carry one.

        The correction runs in ONE direction: a stored verdict may not
        ask more of this identity than the fresh answer does. A stored
        verdict that is more conservative than the fresh answer is kept
        here, so a parent's deliberate restraint survives re-resolution
        and re-serialisation.

        What that does NOT mean: it is a CEILING, not a gate. Whether a
        given delivery path consults the verdict at all is that path's
        decision, and the fresh-read image path deliberately does not --
        criterion 14 makes an unresolvable identity capable for images,
        so a caller-injected reference-replay verdict does not suppress
        an inline image there (``_media_blocks`` says so at the call
        site). Reading this as "a sanitised payload cannot produce image
        bytes anywhere" would be wrong; the guarantee against a
        restricted transport comes from the transport check, not from
        this correction.

        ``block_type`` is corrected on the same terms as ``delivery``.
        Checking only the delivery left the identical hole one FIELD
        over: a stored ``pdf -> typed_block`` whose ``block_type`` said
        ``video`` was handed straight through, and the delivery path
        builds whatever block that names -- so a PDF was delivered as a
        ``VideoContent``, and an image as a ``PdfContent``. Both stored
        and fresh were perceptible, so the delivery rule short-circuited
        and never looked. A typed block whose type does not match the
        one this identity actually resolves is not a milder version of
        the fresh verdict; it is a different, unbuildable one.
        """
        if modality not in self.verdicts:
            return get_delivery_mode(self.identity, modality)
        stored = self.verdicts[modality]
        fresh = get_delivery_mode(self.identity, modality)
        if delivery_demand(stored.delivery) > delivery_demand(fresh.delivery):
            return fresh
        if stored.delivery is not fresh.delivery and _PERCEPTIBLE_DELIVERIES.issuperset(
            {stored.delivery, fresh.delivery}
        ):
            # Same rung, different SHAPE. ``INLINE_IMAGE`` and
            # ``TYPED_BLOCK`` ask no more of a CLI than one another, so
            # the demand check passes them both -- but they are not
            # interchangeable, and a stored ``pdf -> inline_image``
            # survived to cost a capable identity its typed PDF block:
            # the artifact silently degraded to a bare resource
            # reference because nothing can build an image block for a
            # PDF. Not a safety hole; the delivery paths catch it. It is
            # the stored verdict quietly reducing what the caller gets.
            return fresh
        if stored.delivery is DeliveryMode.TYPED_BLOCK and stored.block_type != fresh.block_type:
            return fresh
        return stored

    def to_payload(self) -> dict[str, object]:
        """Serialize to a JSON-compatible dict for session payload persistence.

        Serialises CORRECTED verdicts (via :meth:`verdict_for`), not the
        raw stored ones. Re-emitting an uncorrected verdict would carry a
        stale ``inline_image`` -- or a stale ``block_type`` -- forward
        through every re-serialisation and record it in the session file
        and the wire-ledger capability digest, an audit trail that
        disagrees with what the runtime actually did.

        The transport is canonicalised on the way out too. That is now
        BELT-AND-BRACES rather than the guarantee: since
        ``MultimodalModelIdentity.__post_init__`` canonicalises on
        construction, an identity cannot reach here carrying a spelling
        this would have to fix, and removing the call changes no output.
        Said plainly because the paragraph that used to stand here
        described a live hazard -- one run producing a different
        wire-ledger digest per spelling -- which a later commit closed at
        construction, leaving this reading as a claim about the past.
        """
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
    """Build a pre-computed capability profile for all supported modalities.

    Resolves against a CANONICAL identity. Every verdict quotes the
    transport in its ``reason`` -- text this layer prints and the
    wire-ledger digest is taken over -- so resolving from a raw spelling
    embedded it in five reason strings per profile. Canonicalising the
    transport field alone left those untouched, and one run still
    produced a different digest per spelling.
    """
    canonical = _canonical_identity(identity)
    verdicts = {
        modality: get_delivery_mode(canonical, modality) for modality in SUPPORTED_MODALITIES
    }
    return ResolvedCapabilityProfile(identity=canonical, verdicts=verdicts)


def _canonical_identity(identity: MultimodalModelIdentity) -> MultimodalModelIdentity:
    """Return ``identity`` with its transport in canonical spelling.

    Belt-and-braces, like the ``to_payload`` canonicalisation:
    ``MultimodalModelIdentity.__post_init__`` already does this on
    construction, so this cannot change an identity that exists. Kept as
    a statement of what this function requires of its input, not as a
    guard anything currently depends on.
    """
    canonical = payload_transport(identity.transport)
    if canonical == identity.transport:
        return identity
    return replace(identity, transport=canonical)


def payload_transport(raw: object) -> str | None:
    """Return a usable transport from an untrusted payload field, or ``None``.

    An empty or whitespace-only value is NOT a transport. Treating it as
    one made ``"transport": ""`` outrank an operator's
    ``--agent-transport`` declaration and left the guards blind.

    Lowercased as well as stripped, so every seam that writes a transport
    spelling agrees: each matcher strips and lowercases before comparing,
    so this changes no guard -- it makes the session file and the
    wire-ledger capability digest agree with themselves.

    THE definition, re-exported by
    :mod:`ralph.mcp.server.runtime_session` for its historical callers.
    Two copies of this rule existed, one per package, and the packages
    they served hand identities to each other.
    """
    if not isinstance(raw, str):
        return None
    return raw.strip().lower() or None


def payload_provider(raw: object) -> str:
    """Return a provider name from an untrusted payload field.

    ``str(...)`` is the wrong coercion for a JSON field: it turns a
    payload ``{"provider": null}`` into the literal provider ``'none'``,
    which :meth:`MultimodalModelIdentity.is_known` reads as RESOLVED --
    so the identity-unknown degradation warning is suppressed for an
    identity that has no provider at all, and ``'none'`` is what the
    wire ledger records as the provider served. ``null`` is the natural
    serialisation of "no provider".

    THREE readers coerced this way. Fixing the one in front of me left
    the other two minting ``'none'`` from the same payload, so the rule
    lives here and every reader goes through it.
    """
    return raw if isinstance(raw, str) else UNKNOWN_IDENTITY.provider


def payload_delivery(raw: object) -> DeliveryMode:
    """Return a delivery mode from an untrusted payload field.

    Anything unrecognised -- a misspelling, a mode from a future
    version, a non-string -- becomes a replay handle rather than an
    error: an unreadable stored verdict must not take the artifact away,
    and it must not be coerced with ``str()`` into a mode either.
    """
    if not isinstance(raw, str):
        return DeliveryMode.RESOURCE_REFERENCE_REPLAY
    try:
        return DeliveryMode(raw)
    except ValueError:
        return DeliveryMode.RESOURCE_REFERENCE_REPLAY


def payload_block_type(raw: object) -> str | None:
    """Return a block type from an untrusted payload field, or ``None``.

    The same rule as :func:`payload_model_id`: a non-string is not a
    block type. ``str(...)`` put a JSON object's Python ``repr`` in the
    ``block_type`` field of a persisted verdict, where it reached
    ``to_payload`` and the wire-ledger capability digest.
    """
    return raw if isinstance(raw, str) else None


def payload_model_id(raw: object) -> str | None:
    """Return a model id from an untrusted payload field, or ``None``.

    A non-string is not a model id -- the same rule
    :func:`payload_transport` applies to its own field. The readers
    coerced with ``str(...)``, so a JSON number or object arrived as a
    model id spelled after Python's ``repr``, and that spelling travels
    into every verdict ``reason`` shown to the agent.
    """
    return raw if isinstance(raw, str) else None


def _rehydrated_reason(
    identity: MultimodalModelIdentity,
    modality: str,
    delivery: DeliveryMode,
) -> str:
    """Explain a rehydrated verdict in RALPH's words, never the payload's.

    A verdict's ``reason`` is prose shown to the agent -- ``_media_blocks``
    prints it in the sentence that says why a file was degraded or
    refused, and it is stored in the wire-ledger capability digest. Read
    straight from the payload, that sentence was written by whatever
    wrote the session file: control characters, another provider's name
    beside the corrected one, an arbitrary instruction addressed to the
    agent reading it. The commit that stopped the identity and the
    verdict disagreeing about the provider left the disagreement
    representable one field to the right, in the string that quotes it.

    So the payload's ``reason`` is not read at all. A stored verdict
    that agrees with a fresh resolution gets the fresh explanation --
    identical prose, because both are derived from the same identity --
    and one that disagrees gets a Ralph-authored line naming both. The
    stored DELIVERY still stands; only the words are ours.
    """
    fresh = get_delivery_mode(identity, modality)
    if delivery is fresh.delivery:
        return fresh.reason
    return (
        f"stored verdict '{delivery.value}' rehydrated from the session payload "
        f"for provider '{identity.provider}'; a fresh resolution says "
        f"'{fresh.delivery.value}'"
    )


def profile_from_payload(raw: dict[str, object]) -> ResolvedCapabilityProfile:
    """Rehydrate a ResolvedCapabilityProfile from a serialized session payload dict.

    Every field is read through the payload seams above, and the
    rehydrated verdicts take their ``provider`` and ``model_id`` from
    the CANONICALISED identity rather than from the payload again. Read
    twice, they disagreed: the identity said ``provider='claude'`` while
    the verdict beside it -- the text the agent is actually shown, and
    the value ``_build_warning_block`` quotes -- still said
    ``'  CLAUDE  '``.
    """
    # Normalised on the way IN. A persisted profile is untrusted input
    # like any other, and its transport was copied verbatim onto the
    # caller identity and re-serialised to the grandchild -- so one
    # hand-edited ``'CODEX'`` or ``'  codex  '`` propagated a spelling
    # every matcher then had to strip and lower again, and produced a
    # different capability digest for the same run.
    identity = MultimodalModelIdentity(
        provider=payload_provider(raw.get("provider")),
        model_id=payload_model_id(raw.get("model_id")),
        transport=payload_transport(raw.get("transport")),
    )
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
