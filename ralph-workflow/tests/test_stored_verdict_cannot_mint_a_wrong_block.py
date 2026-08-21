"""A stored verdict is untrusted input in EVERY field, not just ``delivery``.

``verdict_for`` corrects a persisted verdict against the live identity so
a session written before a guard existed -- or by a different transport,
or by hand -- cannot promise a delivery this CLI must not be given. That
correction originally looked at ``delivery`` alone, which left the
identical hole one field over: a stored ``pdf -> typed_block`` naming
``block_type="video"`` was handed straight through, and the delivery path
builds whatever block the verdict names.

Both stored and fresh are perceptible in that case, so the delivery rule
short-circuited and never looked at the block type at all.
"""

from __future__ import annotations

from ralph.mcp.multimodal.artifacts import MODALITY_IMAGE, MODALITY_PDF
from ralph.mcp.multimodal.capabilities import (
    CapabilityVerdict,
    DeliveryMode,
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
    get_delivery_mode,
)

_CLAUDE = MultimodalModelIdentity(
    provider="claude",
    model_id="claude-opus-5",
    transport="claude",
)
_CODEX = MultimodalModelIdentity(provider="openai", model_id="gpt-5", transport="codex")


def _stored(modality: str, delivery: DeliveryMode, **kwargs: object) -> CapabilityVerdict:
    block_type = kwargs.pop("block_type", None)
    assert not kwargs
    return CapabilityVerdict(
        modality=modality,
        delivery=delivery,
        provider="claude",
        model_id="claude-opus-5",
        reason="a value read verbatim out of a session payload",
        block_type=block_type if isinstance(block_type, str) else None,
    )


def test_a_stored_block_type_that_disagrees_is_corrected() -> None:
    """A PDF must not be delivered as a video block."""
    profile = ResolvedCapabilityProfile(
        identity=_CLAUDE,
        verdicts={MODALITY_PDF: _stored(MODALITY_PDF, DeliveryMode.TYPED_BLOCK, block_type="video")},
    )

    corrected = profile.verdict_for(MODALITY_PDF)

    assert corrected.block_type == get_delivery_mode(_CLAUDE, MODALITY_PDF).block_type
    assert corrected.block_type == "pdf"


def test_a_corrected_block_type_does_not_survive_re_serialisation() -> None:
    """``to_payload`` must not carry the bad value forward.

    Re-emitting it would record the wrong block type in the session file
    and the wire-ledger capability digest -- an audit trail that
    disagrees with what the runtime actually did -- and rehydrate it as
    the same untrusted value on the next hop.
    """
    profile = ResolvedCapabilityProfile(
        identity=_CLAUDE,
        verdicts={MODALITY_PDF: _stored(MODALITY_PDF, DeliveryMode.TYPED_BLOCK, block_type="video")},
    )

    payload = profile.to_payload()

    verdicts = payload["verdicts"]
    assert isinstance(verdicts, dict)
    assert verdicts[MODALITY_PDF]["block_type"] == "pdf"


def test_a_stored_reference_replay_does_not_outrank_an_unsupported_answer() -> None:
    """A reference is strictly MORE than nothing, so it needs correcting too.

    Sorting deliveries into "perceptible" and "everything else" made
    ``RESOURCE_REFERENCE_REPLAY`` indistinguishable from
    ``UNSUPPORTED``, so a stored reference verdict survived against an
    identity whose fresh answer was ``UNSUPPORTED`` -- delivering a
    resource reference where a fresh profile returns an error.
    """
    profile = ResolvedCapabilityProfile(
        identity=_CODEX,
        verdicts={
            MODALITY_PDF: CapabilityVerdict(
                modality=MODALITY_PDF,
                delivery=DeliveryMode.RESOURCE_REFERENCE_REPLAY,
                provider="openai",
                model_id="gpt-5",
                reason="a value read verbatim out of a session payload",
            )
        },
    )

    assert get_delivery_mode(_CODEX, MODALITY_PDF).delivery is DeliveryMode.UNSUPPORTED
    assert profile.verdict_for(MODALITY_PDF).delivery is DeliveryMode.UNSUPPORTED


def test_a_conservative_stored_verdict_is_still_kept() -> None:
    """The correction is a ceiling, not a floor: restraint survives."""
    profile = ResolvedCapabilityProfile(
        identity=_CLAUDE,
        verdicts={MODALITY_IMAGE: _stored(MODALITY_IMAGE, DeliveryMode.RESOURCE_REFERENCE_REPLAY)},
    )

    assert profile.verdict_for(MODALITY_IMAGE).delivery is (
        DeliveryMode.RESOURCE_REFERENCE_REPLAY
    )


def test_a_matching_stored_block_type_is_left_alone() -> None:
    """Correction must not fire on a verdict that already agrees.

    Otherwise the rule would discard a parent's verdict wholesale and
    the ``reason`` text it carries -- the string this layer prints for
    operator triage -- on every single lookup.
    """
    profile = ResolvedCapabilityProfile(
        identity=_CLAUDE,
        verdicts={MODALITY_PDF: _stored(MODALITY_PDF, DeliveryMode.TYPED_BLOCK, block_type="pdf")},
    )

    kept = profile.verdict_for(MODALITY_PDF)

    assert kept.reason == "a value read verbatim out of a session payload"
