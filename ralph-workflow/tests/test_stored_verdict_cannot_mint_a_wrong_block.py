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
    profile = ResolvedCapabilityProfile(
        identity=_CLAUDE,
        verdicts={
            MODALITY_PDF: _stored(MODALITY_PDF, DeliveryMode.TYPED_BLOCK, block_type="video")
        },
    )

    corrected = profile.verdict_for(MODALITY_PDF)

    assert corrected.block_type == get_delivery_mode(_CLAUDE, MODALITY_PDF).block_type
    assert corrected.block_type == "pdf"


def test_a_corrected_block_type_does_not_survive_re_serialisation() -> None:
    profile = ResolvedCapabilityProfile(
        identity=_CLAUDE,
        verdicts={
            MODALITY_PDF: _stored(MODALITY_PDF, DeliveryMode.TYPED_BLOCK, block_type="video")
        },
    )

    payload = profile.to_payload()

    verdicts = payload["verdicts"]
    assert isinstance(verdicts, dict)
    assert verdicts[MODALITY_PDF]["block_type"] == "pdf"


def test_a_stored_reference_replay_does_not_outrank_an_unsupported_answer() -> None:
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
    profile = ResolvedCapabilityProfile(
        identity=_CLAUDE,
        verdicts={MODALITY_IMAGE: _stored(MODALITY_IMAGE, DeliveryMode.RESOURCE_REFERENCE_REPLAY)},
    )

    assert profile.verdict_for(MODALITY_IMAGE).delivery is (DeliveryMode.RESOURCE_REFERENCE_REPLAY)


def test_a_matching_stored_block_type_is_left_alone() -> None:
    profile = ResolvedCapabilityProfile(
        identity=_CLAUDE,
        verdicts={MODALITY_PDF: _stored(MODALITY_PDF, DeliveryMode.TYPED_BLOCK, block_type="pdf")},
    )

    kept = profile.verdict_for(MODALITY_PDF)

    assert kept.reason == "a value read verbatim out of a session payload"


def test_a_model_flag_cannot_outrun_the_cli_it_travels_through() -> None:
    gemini_on_claude = MultimodalModelIdentity(
        provider="gemini", model_id="gemini-2.5-pro", transport="claude"
    )
    gemini_on_its_own_cli = MultimodalModelIdentity(
        provider="gemini", model_id="gemini-2.5-pro", transport="agy"
    )

    for modality in ("audio", "video"):
        bounded = get_delivery_mode(gemini_on_claude, modality)
        native = get_delivery_mode(gemini_on_its_own_cli, modality)

        assert native.delivery is DeliveryMode.TYPED_BLOCK, modality
        assert bounded.delivery is DeliveryMode.RESOURCE_REFERENCE_REPLAY, modality

    on_codex = MultimodalModelIdentity(
        provider="anthropic", model_id="claude-opus-5", transport="codex"
    )
    assert get_delivery_mode(on_codex, "pdf").delivery is (DeliveryMode.RESOURCE_REFERENCE_REPLAY)

    native_pair = MultimodalModelIdentity(provider="claude", model_id="opus", transport="claude")
    assert get_delivery_mode(native_pair, "pdf").delivery is DeliveryMode.TYPED_BLOCK
