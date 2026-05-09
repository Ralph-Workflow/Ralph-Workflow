"""Direct assertions for multimodal capability detection.

Tests every major modality class, provider/transport combination, and
verdict helper method. This is the primary verification suite for the
capabilities module — the single source of truth for delivery decisions.
"""

from __future__ import annotations

import pytest

from ralph.mcp.multimodal.artifacts import (
    MODALITY_AUDIO,
    MODALITY_DOCUMENT,
    MODALITY_IMAGE,
    MODALITY_PDF,
    MODALITY_VIDEO,
    SUPPORTED_MODALITIES,
)
from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    CapabilityVerdict,
    DeliveryMode,
    MultimodalModelIdentity,
    get_delivery_mode,
)

# ---------------------------------------------------------------------------
# MultimodalModelIdentity helpers
# ---------------------------------------------------------------------------


def test_unknown_identity_is_not_known() -> None:
    assert not UNKNOWN_IDENTITY.is_known()


def test_known_provider_is_known() -> None:
    assert MultimodalModelIdentity(provider="claude").is_known()
    assert MultimodalModelIdentity(provider="openai").is_known()
    assert MultimodalModelIdentity(provider="gemini").is_known()


def test_explicit_unknown_provider_is_not_known() -> None:
    assert not MultimodalModelIdentity(provider="unknown").is_known()


# ---------------------------------------------------------------------------
# CapabilityVerdict helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("delivery,expected_inline,expected_rr,expected_typed,expected_supported", [
    (DeliveryMode.INLINE_IMAGE, True, False, False, True),
    (DeliveryMode.RESOURCE_REFERENCE_REPLAY, False, True, False, True),
    (DeliveryMode.UNSUPPORTED, False, False, False, False),
    (DeliveryMode.TYPED_BLOCK, False, False, True, True),
    (DeliveryMode.PRESERVED_ONLY, False, False, False, True),
])
def test_capability_verdict_helpers(
    delivery: DeliveryMode,
    expected_inline: bool,
    expected_rr: bool,
    expected_typed: bool,
    expected_supported: bool,
) -> None:
    verdict = CapabilityVerdict(
        modality="image", delivery=delivery, provider="test"
    )
    assert verdict.is_inline() == expected_inline
    assert verdict.is_resource_reference() == expected_rr
    assert verdict.is_typed_block() == expected_typed
    assert verdict.is_supported() == expected_supported


# ---------------------------------------------------------------------------
# Unknown modality → UNSUPPORTED for any provider
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["claude", "openai", "gemini", "unknown"])
def test_unknown_modality_is_unsupported(provider: str) -> None:
    identity = MultimodalModelIdentity(provider=provider)
    verdict = get_delivery_mode(identity, "not_a_real_modality")
    assert verdict.delivery == DeliveryMode.UNSUPPORTED
    assert not verdict.is_supported()
    assert "unknown modality" in verdict.reason


# ---------------------------------------------------------------------------
# Unknown provider → RESOURCE_REFERENCE for every supported modality
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("modality", sorted(SUPPORTED_MODALITIES))
def test_unknown_provider_defaults_to_resource_reference_replay(modality: str) -> None:
    verdict = get_delivery_mode(UNKNOWN_IDENTITY, modality)
    assert verdict.delivery == DeliveryMode.RESOURCE_REFERENCE_REPLAY
    assert verdict.is_supported()
    assert verdict.is_resource_reference()
    assert "unknown provider" in verdict.reason.lower()


# ---------------------------------------------------------------------------
# Claude provider — all modalities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model_id", [None, "claude-3-5-sonnet-20241022", "claude-opus-4-7"])
def test_claude_image_is_inline(model_id: str | None) -> None:
    identity = MultimodalModelIdentity(provider="claude", model_id=model_id)
    verdict = get_delivery_mode(identity, MODALITY_IMAGE)
    assert verdict.delivery == DeliveryMode.INLINE_IMAGE
    assert verdict.is_inline()
    assert verdict.is_supported()


@pytest.mark.parametrize(
    "modality", [MODALITY_PDF, MODALITY_DOCUMENT]
)
def test_claude_pdf_and_document_are_typed_block(modality: str) -> None:
    """Claude supports PDF and document modalities via typed document blocks."""
    identity = MultimodalModelIdentity(provider="claude", model_id="claude-3-5-sonnet-20241022")
    verdict = get_delivery_mode(identity, modality)
    assert verdict.delivery == DeliveryMode.TYPED_BLOCK
    assert verdict.is_typed_block()
    assert verdict.is_supported()
    assert verdict.block_type == modality


@pytest.mark.parametrize(
    "modality", [MODALITY_AUDIO, MODALITY_VIDEO]
)
def test_claude_av_modalities_are_unsupported(modality: str) -> None:
    """Claude's API does not accept audio or video input."""
    identity = MultimodalModelIdentity(provider="claude", model_id="claude-3-5-sonnet-20241022")
    verdict = get_delivery_mode(identity, modality)
    assert verdict.delivery == DeliveryMode.UNSUPPORTED
    assert not verdict.is_supported()


def test_claude_anthropic_alias_also_supports_inline_image() -> None:
    identity = MultimodalModelIdentity(provider="anthropic")
    verdict = get_delivery_mode(identity, MODALITY_IMAGE)
    assert verdict.delivery == DeliveryMode.INLINE_IMAGE


# ---------------------------------------------------------------------------
# OpenAI / codex provider — image modality
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider,model_id", [
    ("openai", "gpt-4o"),
    ("openai", "gpt-4o-mini"),
    ("openai", "gpt-4-vision-preview"),
    ("openai", "gpt-4-turbo"),
    ("openai", "gpt-4-0125-preview"),
    ("openai", "o1-preview"),
    ("openai", "o3-mini"),
    ("codex", "gpt-4o"),
    ("codex", None),  # unknown model defaults to inline
])
def test_openai_vision_model_image_is_inline(provider: str, model_id: str | None) -> None:
    identity = MultimodalModelIdentity(provider=provider, model_id=model_id)
    verdict = get_delivery_mode(identity, MODALITY_IMAGE)
    assert verdict.delivery == DeliveryMode.INLINE_IMAGE
    assert verdict.is_inline()


def test_openai_non_vision_model_image_is_resource_reference_replay() -> None:
    identity = MultimodalModelIdentity(provider="openai", model_id="gpt-3.5-turbo")
    verdict = get_delivery_mode(identity, MODALITY_IMAGE)
    assert verdict.delivery == DeliveryMode.RESOURCE_REFERENCE_REPLAY
    assert verdict.is_resource_reference()


@pytest.mark.parametrize(
    "modality", [MODALITY_AUDIO, MODALITY_VIDEO]
)
def test_openai_av_modalities_are_unsupported(modality: str) -> None:
    identity = MultimodalModelIdentity(provider="openai", model_id="gpt-4o")
    verdict = get_delivery_mode(identity, modality)
    assert verdict.delivery == DeliveryMode.UNSUPPORTED
    assert not verdict.is_supported()


@pytest.mark.parametrize(
    "modality", [MODALITY_PDF, MODALITY_DOCUMENT]
)
def test_openai_pdf_and_document_are_unsupported(modality: str) -> None:
    """OpenAI chat API cannot process PDFs or documents as raw bytes.

    Returning UNSUPPORTED gives the agent an explicit, actionable failure
    instead of a resource_reference that the model cannot use.
    """
    identity = MultimodalModelIdentity(provider="openai", model_id="gpt-4o")
    verdict = get_delivery_mode(identity, modality)
    assert verdict.delivery == DeliveryMode.UNSUPPORTED
    assert not verdict.is_supported()


@pytest.mark.parametrize(
    "modality", [MODALITY_PDF, MODALITY_DOCUMENT]
)
def test_codex_pdf_and_document_are_unsupported(modality: str) -> None:
    identity = MultimodalModelIdentity(provider="codex", model_id="gpt-4o")
    verdict = get_delivery_mode(identity, modality)
    assert verdict.delivery == DeliveryMode.UNSUPPORTED
    assert not verdict.is_supported()


# ---------------------------------------------------------------------------
# Gemini provider — all modalities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model_id", [None, "gemini-2.0-flash", "gemini-pro-vision"])
def test_gemini_image_is_inline(model_id: str | None) -> None:
    identity = MultimodalModelIdentity(provider="gemini", model_id=model_id)
    verdict = get_delivery_mode(identity, MODALITY_IMAGE)
    assert verdict.delivery == DeliveryMode.INLINE_IMAGE
    assert verdict.is_inline()


@pytest.mark.parametrize(
    "modality", [MODALITY_PDF, MODALITY_AUDIO, MODALITY_VIDEO, MODALITY_DOCUMENT]
)
def test_gemini_non_image_modalities_are_typed_block(modality: str) -> None:
    identity = MultimodalModelIdentity(provider="gemini")
    verdict = get_delivery_mode(identity, modality)
    assert verdict.delivery == DeliveryMode.TYPED_BLOCK
    assert verdict.is_typed_block()
    assert verdict.is_supported()
    assert verdict.block_type == modality


# ---------------------------------------------------------------------------
# Mixed-modality: same identity, multiple modalities
# ---------------------------------------------------------------------------


def test_claude_mixed_modality_verdicts_are_consistent() -> None:
    identity = MultimodalModelIdentity(provider="claude", model_id="claude-3-5-sonnet-20241022")
    modalities = [MODALITY_IMAGE, MODALITY_PDF, MODALITY_AUDIO, MODALITY_VIDEO, MODALITY_DOCUMENT]
    verdicts = [get_delivery_mode(identity, m) for m in modalities]
    image_v, pdf_v, audio_v, video_v, doc_v = verdicts
    # Image: inline_image; pdf/document: typed_block; audio/video: unsupported
    assert image_v.is_inline()
    assert pdf_v.is_typed_block()
    assert doc_v.is_typed_block()
    assert audio_v.delivery == DeliveryMode.UNSUPPORTED
    assert video_v.delivery == DeliveryMode.UNSUPPORTED


def test_unknown_provider_all_modalities_all_supported() -> None:
    """Unknown provider: all modalities must be resource_reference_replay, never blocked."""
    for modality in SUPPORTED_MODALITIES:
        verdict = get_delivery_mode(UNKNOWN_IDENTITY, modality)
        assert verdict.is_supported(), f"modality {modality} blocked for unknown provider"
        assert verdict.is_resource_reference()


# ---------------------------------------------------------------------------
# Verdict fields are populated correctly
# ---------------------------------------------------------------------------


def test_verdict_fields_are_populated() -> None:
    identity = MultimodalModelIdentity(provider="claude", model_id="claude-3-5-sonnet-20241022")
    verdict = get_delivery_mode(identity, MODALITY_IMAGE)
    assert verdict.modality == MODALITY_IMAGE
    assert verdict.provider == "claude"
    assert verdict.model_id == "claude-3-5-sonnet-20241022"
    assert verdict.reason != ""


def test_unsupported_verdict_cites_modality_name() -> None:
    identity = MultimodalModelIdentity(provider="claude")
    verdict = get_delivery_mode(identity, "hologram")
    assert "hologram" in verdict.reason


# ---------------------------------------------------------------------------
# MultimodalFailure taxonomy
# ---------------------------------------------------------------------------


def test_multimodal_failure_kinds_are_importable() -> None:
    from ralph.mcp.multimodal.errors import (  # noqa: PLC0415
        MultimodalFailure,
        MultimodalFailureKind,
    )
    assert MultimodalFailureKind.UNSUPPORTED_MODALITY == "unsupported_modality"
    assert MultimodalFailureKind.UNSUPPORTED_RUNTIME_SEAM == "unsupported_runtime_seam"
    assert MultimodalFailureKind.UNSUPPORTED_MIME_TYPE == "unsupported_mime_type"
    assert MultimodalFailureKind.PAYLOAD_TOO_LARGE == "payload_too_large"
    assert MultimodalFailureKind.FILE_READ_ERROR == "file_read_error"
    assert MultimodalFailureKind.NO_ACTIVE_MANIFEST == "no_active_manifest"
    assert MultimodalFailureKind.PROVIDER_REJECTED == "provider_rejected"
    assert MultimodalFailureKind.INVALID_REPLAY_HANDLE == "invalid_replay_handle"
    assert MultimodalFailureKind.MISSING_REPLAY_SOURCE == "missing_replay_source"
    f = MultimodalFailure(
        kind=MultimodalFailureKind.UNSUPPORTED_MODALITY,
        message="audio not supported",
        modality="audio",
        provider="claude",
    )
    assert "audio not supported" in f.user_message()
    assert "modality: audio" in f.user_message()
    assert "provider: claude" in f.user_message()


def test_multimodal_failure_is_exported_from_package() -> None:
    from ralph.mcp.multimodal import MultimodalFailure, MultimodalFailureKind  # noqa: PLC0415
    assert MultimodalFailure is not None
    assert MultimodalFailureKind is not None


def test_multimodal_failure_user_message_without_optional_fields() -> None:
    from ralph.mcp.multimodal.errors import (  # noqa: PLC0415
        MultimodalFailure,
        MultimodalFailureKind,
    )
    f = MultimodalFailure(
        kind=MultimodalFailureKind.FILE_READ_ERROR,
        message="file not found",
    )
    assert f.user_message() == "file not found"


# ---------------------------------------------------------------------------
# Named contract tests required by the managed-runtime acceptance plan
# ---------------------------------------------------------------------------


def test_capability_verdict_carries_block_type_for_typed_modalities() -> None:
    """Every TYPED_BLOCK verdict must carry a non-empty block_type string.

    This proves that downstream layers (tool handlers, prompt sidecar) can rely
    on block_type being set whenever the delivery mode is TYPED_BLOCK, without
    re-inspecting the provider or modality.
    """
    typed_cases = [
        (MultimodalModelIdentity(provider="claude"), "pdf"),
        (MultimodalModelIdentity(provider="claude"), "document"),
        (MultimodalModelIdentity(provider="gemini"), "pdf"),
        (MultimodalModelIdentity(provider="gemini"), "document"),
        (MultimodalModelIdentity(provider="gemini"), "audio"),
        (MultimodalModelIdentity(provider="gemini"), "video"),
    ]
    for identity, modality in typed_cases:
        verdict = get_delivery_mode(identity, modality)
        assert verdict.delivery == DeliveryMode.TYPED_BLOCK, (
            f"provider={identity.provider!r} modality={modality!r}: expected TYPED_BLOCK, "
            f"got {verdict.delivery!r}"
        )
        assert verdict.block_type is not None and verdict.block_type != "", (
            f"provider={identity.provider!r} modality={modality!r}: block_type must be set "
            f"for TYPED_BLOCK delivery, got block_type={verdict.block_type!r}"
        )
        assert verdict.block_type == modality, (
            f"provider={identity.provider!r} modality={modality!r}: block_type must equal "
            f"modality for current typed-block support; got {verdict.block_type!r}"
        )


def test_capability_verdict_reason_is_explicit_for_unsupported_modality() -> None:
    """UNSUPPORTED verdicts must carry a non-empty reason that names the modality.

    This ensures agents and workflows receive an explicit, actionable message
    rather than a silent failure when a modality is not supported.
    """
    unsupported_cases = [
        (MultimodalModelIdentity(provider="claude"), "audio"),
        (MultimodalModelIdentity(provider="claude"), "video"),
        (MultimodalModelIdentity(provider="openai", model_id="gpt-4o"), "audio"),
        (MultimodalModelIdentity(provider="openai", model_id="gpt-4o"), "video"),
        (MultimodalModelIdentity(provider="openai", model_id="gpt-4o"), "pdf"),
        (MultimodalModelIdentity(provider="openai", model_id="gpt-4o"), "document"),
    ]
    for identity, modality in unsupported_cases:
        verdict = get_delivery_mode(identity, modality)
        assert verdict.delivery == DeliveryMode.UNSUPPORTED, (
            f"provider={identity.provider!r} modality={modality!r}: expected UNSUPPORTED, "
            f"got {verdict.delivery!r}"
        )
        assert verdict.reason != "", (
            f"provider={identity.provider!r} modality={modality!r}: reason must not be empty "
            f"for UNSUPPORTED delivery"
        )
        assert modality in verdict.reason, (
            f"provider={identity.provider!r} modality={modality!r}: reason must name the "
            f"modality so agents can diagnose the failure; reason={verdict.reason!r}"
        )


# ---------------------------------------------------------------------------
# ResolvedCapabilityProfile
# ---------------------------------------------------------------------------


def test_resolve_capability_profile_contains_all_supported_modalities() -> None:
    from ralph.mcp.multimodal.capabilities import resolve_capability_profile  # noqa: PLC0415

    identity = MultimodalModelIdentity(provider="claude", model_id="claude-3-5-sonnet-20241022")
    profile = resolve_capability_profile(identity)
    assert set(profile.verdicts.keys()) == SUPPORTED_MODALITIES


def test_resolve_capability_profile_verdict_for_known_modality() -> None:
    from ralph.mcp.multimodal.capabilities import resolve_capability_profile  # noqa: PLC0415

    identity = MultimodalModelIdentity(provider="claude")
    profile = resolve_capability_profile(identity)
    verdict = profile.verdict_for(MODALITY_IMAGE)
    assert verdict.delivery == DeliveryMode.INLINE_IMAGE


def test_resolve_capability_profile_verdict_for_unknown_modality_computes_fresh() -> None:
    from ralph.mcp.multimodal.capabilities import resolve_capability_profile  # noqa: PLC0415

    identity = MultimodalModelIdentity(provider="claude")
    profile = resolve_capability_profile(identity)
    verdict = profile.verdict_for("not_a_real_modality")
    assert verdict.delivery == DeliveryMode.UNSUPPORTED


def test_resolve_capability_profile_unknown_provider_all_resource_reference() -> None:
    from ralph.mcp.multimodal.capabilities import resolve_capability_profile  # noqa: PLC0415

    profile = resolve_capability_profile(UNKNOWN_IDENTITY)
    for modality in SUPPORTED_MODALITIES:
        verdict = profile.verdict_for(modality)
        assert verdict.delivery == DeliveryMode.RESOURCE_REFERENCE_REPLAY, (
            f"unknown provider modality={modality!r}: expected RESOURCE_REFERENCE_REPLAY"
        )


@pytest.mark.parametrize("provider,model_id,modality,expected_delivery", [
    ("claude", "claude-opus-4-7", MODALITY_IMAGE, DeliveryMode.INLINE_IMAGE),
    ("claude", "claude-opus-4-7", MODALITY_PDF, DeliveryMode.TYPED_BLOCK),
    ("claude", "claude-opus-4-7", MODALITY_AUDIO, DeliveryMode.UNSUPPORTED),
    ("openai", "gpt-4o", MODALITY_IMAGE, DeliveryMode.INLINE_IMAGE),
    ("openai", "gpt-4o", MODALITY_PDF, DeliveryMode.UNSUPPORTED),
    ("gemini", "gemini-2.0-flash", MODALITY_AUDIO, DeliveryMode.TYPED_BLOCK),
    ("gemini", "gemini-2.0-flash", MODALITY_VIDEO, DeliveryMode.TYPED_BLOCK),
])
def test_resolve_capability_profile_provider_modality_coverage(
    provider: str,
    model_id: str,
    modality: str,
    expected_delivery: DeliveryMode,
) -> None:
    from ralph.mcp.multimodal.capabilities import resolve_capability_profile  # noqa: PLC0415

    identity = MultimodalModelIdentity(provider=provider, model_id=model_id)
    profile = resolve_capability_profile(identity)
    verdict = profile.verdict_for(modality)
    assert verdict.delivery == expected_delivery, (
        f"provider={provider!r} modality={modality!r}: expected {expected_delivery!r}, "
        f"got {verdict.delivery!r}"
    )


def test_resolved_capability_profile_to_payload_roundtrip() -> None:
    """profile_from_payload(profile.to_payload()) restores the same verdicts."""
    from ralph.mcp.multimodal.capabilities import (  # noqa: PLC0415
        profile_from_payload,
        resolve_capability_profile,
    )

    identity = MultimodalModelIdentity(provider="gemini", model_id="gemini-2.0-flash")
    original = resolve_capability_profile(identity)
    payload = original.to_payload()
    restored = profile_from_payload(payload)

    for modality in SUPPORTED_MODALITIES:
        orig_v = original.verdict_for(modality)
        rest_v = restored.verdict_for(modality)
        assert orig_v.delivery == rest_v.delivery, (
            f"modality={modality!r}: delivery mismatch after roundtrip"
        )
        assert orig_v.block_type == rest_v.block_type, (
            f"modality={modality!r}: block_type mismatch after roundtrip"
        )


def test_profile_from_payload_missing_verdicts_falls_back_to_computed() -> None:
    """profile_from_payload with no verdicts key falls back to get_delivery_mode."""
    from ralph.mcp.multimodal.capabilities import profile_from_payload  # noqa: PLC0415

    raw: dict[str, object] = {"provider": "claude", "model_id": None, "transport": None}
    profile = profile_from_payload(raw)
    assert profile.verdict_for(MODALITY_IMAGE).delivery == DeliveryMode.INLINE_IMAGE


def test_profile_from_payload_bad_delivery_value_defaults_to_resource_reference() -> None:
    """profile_from_payload with an unrecognized delivery string uses RESOURCE_REFERENCE."""
    from ralph.mcp.multimodal.capabilities import profile_from_payload  # noqa: PLC0415

    raw: dict[str, object] = {
        "provider": "unknown",
        "model_id": None,
        "transport": None,
        "verdicts": {
            MODALITY_IMAGE: {"delivery": "totally_invalid", "reason": "", "block_type": None},
        },
    }
    profile = profile_from_payload(raw)
    assert profile.verdict_for(MODALITY_IMAGE).delivery == DeliveryMode.RESOURCE_REFERENCE_REPLAY
