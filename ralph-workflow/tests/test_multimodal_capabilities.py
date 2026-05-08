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


@pytest.mark.parametrize("delivery,expected_inline,expected_rr,expected_supported", [
    (DeliveryMode.INLINE, True, False, True),
    (DeliveryMode.RESOURCE_REFERENCE, False, True, True),
    (DeliveryMode.UNSUPPORTED, False, False, False),
    (DeliveryMode.UNKNOWN, False, False, True),
])
def test_capability_verdict_helpers(
    delivery: DeliveryMode,
    expected_inline: bool,
    expected_rr: bool,
    expected_supported: bool,
) -> None:
    verdict = CapabilityVerdict(
        modality="image", delivery=delivery, provider="test"
    )
    assert verdict.is_inline() == expected_inline
    assert verdict.is_resource_reference() == expected_rr
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
def test_unknown_provider_defaults_to_resource_reference(modality: str) -> None:
    verdict = get_delivery_mode(UNKNOWN_IDENTITY, modality)
    assert verdict.delivery == DeliveryMode.RESOURCE_REFERENCE
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
    assert verdict.delivery == DeliveryMode.INLINE
    assert verdict.is_inline()
    assert verdict.is_supported()


@pytest.mark.parametrize(
    "modality", [MODALITY_PDF, MODALITY_AUDIO, MODALITY_VIDEO, MODALITY_DOCUMENT]
)
def test_claude_non_image_modalities_are_resource_reference(modality: str) -> None:
    identity = MultimodalModelIdentity(provider="claude", model_id="claude-3-5-sonnet-20241022")
    verdict = get_delivery_mode(identity, modality)
    assert verdict.delivery == DeliveryMode.RESOURCE_REFERENCE
    assert verdict.is_resource_reference()
    assert verdict.is_supported()


def test_claude_anthropic_alias_also_supports_inline_image() -> None:
    identity = MultimodalModelIdentity(provider="anthropic")
    verdict = get_delivery_mode(identity, MODALITY_IMAGE)
    assert verdict.delivery == DeliveryMode.INLINE


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
    assert verdict.delivery == DeliveryMode.INLINE
    assert verdict.is_inline()


def test_openai_non_vision_model_image_is_resource_reference() -> None:
    identity = MultimodalModelIdentity(provider="openai", model_id="gpt-3.5-turbo")
    verdict = get_delivery_mode(identity, MODALITY_IMAGE)
    assert verdict.delivery == DeliveryMode.RESOURCE_REFERENCE
    assert verdict.is_resource_reference()


@pytest.mark.parametrize(
    "modality", [MODALITY_PDF, MODALITY_AUDIO, MODALITY_VIDEO, MODALITY_DOCUMENT]
)
def test_openai_non_image_modalities_are_resource_reference(modality: str) -> None:
    identity = MultimodalModelIdentity(provider="openai", model_id="gpt-4o")
    verdict = get_delivery_mode(identity, modality)
    assert verdict.delivery == DeliveryMode.RESOURCE_REFERENCE
    assert verdict.is_supported()


# ---------------------------------------------------------------------------
# Gemini provider — all modalities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model_id", [None, "gemini-2.0-flash", "gemini-pro-vision"])
def test_gemini_image_is_inline(model_id: str | None) -> None:
    identity = MultimodalModelIdentity(provider="gemini", model_id=model_id)
    verdict = get_delivery_mode(identity, MODALITY_IMAGE)
    assert verdict.delivery == DeliveryMode.INLINE
    assert verdict.is_inline()


@pytest.mark.parametrize(
    "modality", [MODALITY_PDF, MODALITY_AUDIO, MODALITY_VIDEO, MODALITY_DOCUMENT]
)
def test_gemini_non_image_modalities_are_resource_reference(modality: str) -> None:
    identity = MultimodalModelIdentity(provider="gemini")
    verdict = get_delivery_mode(identity, modality)
    assert verdict.delivery == DeliveryMode.RESOURCE_REFERENCE
    assert verdict.is_supported()


# ---------------------------------------------------------------------------
# Mixed-modality: same identity, multiple modalities
# ---------------------------------------------------------------------------


def test_claude_mixed_modality_verdicts_are_consistent() -> None:
    identity = MultimodalModelIdentity(provider="claude", model_id="claude-3-5-sonnet-20241022")
    modalities = [MODALITY_IMAGE, MODALITY_PDF, MODALITY_AUDIO, MODALITY_VIDEO, MODALITY_DOCUMENT]
    verdicts = [get_delivery_mode(identity, m) for m in modalities]
    # All supported
    assert all(v.is_supported() for v in verdicts)
    # Image inline, rest resource_reference
    assert verdicts[0].is_inline()
    for v in verdicts[1:]:
        assert v.is_resource_reference()


def test_unknown_provider_all_modalities_all_supported() -> None:
    """Unknown provider must expose all modalities as resource_reference, never block them."""
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
