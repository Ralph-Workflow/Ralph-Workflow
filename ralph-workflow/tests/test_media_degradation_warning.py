"""Tests for the multimodal graceful-degradation warning seam (S-7 / criterion 3, S-6 / criterion 17).

Per ``.agent/PRODUCT_CRITERIA.md`` (criterion 3): "Treat a multimodal
model as ASSUMED to be present. If it turns out to be missing,
DEGRADE GRACEFULLY WITH A WARNING -- but the default assumption is
always that multimodal works."

Per S-6 (criterion 17): inline image delivery is unconditional for
``INLINE_IMAGE_MIME_TYPES`` payloads that fit the inline cap --
the capability verdict is no longer a gate for image-only inline
delivery (criterion 14: unresolvable -> capable). The
identity_unknown -> warn-block prepending only applies to the
resource_reference path; INLINE_IMAGE-eligible files bypass the
identity check entirely.

These tests pin the warning-block shape so a future regression that
silently dropped an UNKNOWN_IDENTITY case -- emitting a bare
``resource_reference`` with no operator-visible warning -- would fail.
The default assumption is unchanged: a Claude/Gemini identity under a
supported modality returns its inline / typed block with NO warning
block; only UNKNOWN_IDENTITY or UNSUPPORTED verdicts add the warning.
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from loguru import logger as loguru_logger

from ralph.mcp.multimodal.artifacts import ResourceReferenceContent
from ralph.mcp.multimodal.capabilities import (
    CapabilityVerdict,
    DeliveryMode,
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
)
from ralph.mcp.tools.coordination import (
    ImageContent,
    ToolContent,
)
from ralph.mcp.tools.workspace import _media_blocks as media_blocks_module
from tests.mock_session_with_manifest import MockSessionWithManifest

_handle_workspace_media = media_blocks_module._handle_workspace_media
_build_warning_block = media_blocks_module._build_warning_block


def _set_profile(session: object, profile) -> None:
    """Inject a capability profile onto a mock session.

    Sets the session's identity to the profile's as well. A real session
    derives both from one plan, so they always agree; leaving them
    disagreeing made the fixture exercise a shape production cannot
    produce, and the delivery layer legitimately re-resolves such a
    profile against the identity it is actually serving.
    """
    session.capability_profile = profile
    session.model_identity = profile.identity


if TYPE_CHECKING:
    import pytest

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880

PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _png_bytes() -> bytes:
    return base64.b64decode(PNG_1X1_BASE64)


def _write_png(tmp_path: Path, name: str = "smoke.png") -> Path:
    target = tmp_path / name
    target.write_bytes(_png_bytes())
    return target


def _write_pdf(tmp_path: Path, name: str = "smoke.pdf") -> Path:
    """Write a minimal valid PDF stub (header + %%EOF) so MIME inference accepts it."""
    target = tmp_path / name
    target.write_bytes(b"%PDF-1.4\n%fake-test-pdf\n%%EOF\n")
    return target


def test_warning_block_names_provider_modality_and_reason() -> None:
    """The warning block names the provider, modality, and verdict reason explicitly."""
    block = _build_warning_block(
        provider="unknown",
        modality="image",
        verdict_reason="unknown provider",
    )
    text = block.text
    assert "WARNING" in text
    assert "unknown" in text
    assert "image" in text
    assert "unknown provider" in text
    # Criterion 3 wording: "Treat a multimodal model as ASSUMED-present".
    assert "ASSUMED" in text or "assumed" in text


def test_warning_block_names_provider_model_id_and_delivery_mode() -> None:
    """S-6 (criterion 17): the warning block must name provider, model_id, and delivery_mode."""
    block = _build_warning_block(
        provider="claude",
        model_id="claude-opus-4-7",
        modality="image",
        delivery_mode="unsupported",
        verdict_reason="Claude does not accept this modality via Ralph's managed MCP path",
    )
    text = block.text
    assert "WARNING" in text
    assert "claude" in text
    assert "claude-opus-4-7" in text
    assert "unsupported" in text
    assert "image" in text


def test_unknown_identity_for_image_emits_warning_then_resource_reference(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """UNKNOWN_IDENTITY degrades with a warning, then emits a resource-reference block.

    Criterion 3: default assumption is multimodal-present; the
    only signal of degradation is the warning block -- the artifact
    is still delivered as a replayable resource reference so the
    agent can proceed.

    S-6 (criterion 17): the identity_unknown -> warn-block prepending
    is only used for the resource_reference path. An INLINE_IMAGE-
    eligible file would now bypass the warning path entirely. To
    exercise the resource_reference path this test uses a PDF --
    a non-inline-eligible modality whose verdict is
    ``RESOURCE_REFERENCE_REPLAY`` for an unknown identity.
    """

    media_path = _write_pdf(tmp_path)
    session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)
    identity = MultimodalModelIdentity(provider="unknown")
    profile = ResolvedCapabilityProfile(
        identity=identity,
        verdicts={
            "pdf": CapabilityVerdict(
                modality="pdf",
                delivery=DeliveryMode.RESOURCE_REFERENCE_REPLAY,
                provider="unknown",
                model_id=None,
                reason="unknown provider \u2014 defaulting to resource_reference_replay",
            ),
        },
    )
    _set_profile(session, profile)

    ws = MagicMock()
    ws.absolute_path.return_value = str(media_path)
    result = _handle_workspace_media(
        session,
        ws,
        "smoke.pdf",
        max_inline_bytes=DEFAULT_MAX_INLINE_BYTES,
    )
    assert result.is_error is False
    # First content block is the warning.
    first_text = result.content[0].text
    assert "WARNING" in first_text
    assert "unknown" in first_text
    assert "pdf" in first_text
    # A subsequent block is the resource reference.
    ref_present = any(
        getattr(block, "type", None) == "resource_reference"
        or "resource_reference" in str(getattr(block, "type", ""))
        for block in result.content
    )
    assert ref_present


def test_unsupported_verdict_emits_warning_then_resource_reference_fallback(
    tmp_path: Path,
) -> None:
    """S-6 (criterion 17): an UNSUPPORTED verdict still degrades gracefully, with a warning + usable fallback.

    Unlike S-7 (criterion 3) where the warning is paired with the
    existing structured error text, the S-6 graceful-degradation
    path emits a WARNING block followed by a usable
    ``ResourceReferenceContent`` fallback. ``is_error`` is False --
    the call still surfaces multimodal-shaped content the agent
    can act on, and the warning carries the operator-visible
    explanation naming the provider, model_id, and delivery_mode
    that the capability layer reported.
    """
    _write_png(tmp_path)
    session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)
    profile = ResolvedCapabilityProfile(
        identity=MultimodalModelIdentity(provider="claude", model_id="claude-opus-4-7"),
        verdicts={
            "image": CapabilityVerdict(
                modality="image",
                delivery=DeliveryMode.UNSUPPORTED,
                provider="claude",
                model_id="claude-opus-4-7",
                reason=(
                    "Claude does not accept this modality via "
                    "Ralph's managed MCP path (modality: image)"
                ),
            ),
        },
    )
    _set_profile(session, profile)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(_png_bytes())
        try:
            ws = MagicMock()
            ws.absolute_path.return_value = f.name
            # PA-014: pytest's caplog does NOT capture loguru's bound
            # record. We use the loguru sink pattern instead so the
            # structured WARNING record from ``_build_warning_block``
            # is observable via ``message.record['message']``.
            captured: list[object] = []

            def _sink(message: object) -> None:
                captured.append(message)

            handler_id = loguru_logger.add(_sink, level="WARNING")
            try:
                result = _handle_workspace_media(
                    session,
                    ws,
                    "smoke.png",
                    max_inline_bytes=DEFAULT_MAX_INLINE_BYTES,
                )
            finally:
                loguru_logger.remove(handler_id)
            # S-6: the call is NOT an error -- the resource-reference
            # fallback is the graceful-degradation payload, not a
            # structured failure.
            assert result.is_error is False
            # First content block is the WARNING, naming provider,
            # model_id, and delivery_mode.
            assert len(result.content) >= 2
            first_block = result.content[0]
            assert isinstance(first_block, ToolContent)
            first_text = first_block.text
            assert "WARNING" in first_text
            assert "claude" in first_text
            assert "claude-opus-4-7" in first_text
            assert "unsupported" in first_text
            # Subsequent block is a usable ResourceReferenceContent.
            fallback = result.content[1]
            assert isinstance(fallback, ResourceReferenceContent)
            assert fallback.uri
            assert fallback.mime_type == "image/png"
            assert fallback.modality == "image"
            # A matching logger.warning was emitted by the
            # ``_build_warning_block`` helper.
            matching = [
                message
                for message in captured
                if "multimodal degraded" in str(message.record["message"])
                and "claude" in str(message.record["message"])
            ]
            assert matching, (
                "expected a matching WARNING log record for the UNSUPPORTED "
                "verdict from the _build_warning_block helper"
            )
        finally:
            Path(f.name).unlink(missing_ok=True)


def test_inline_eligible_image_with_known_openai_identity_emits_image_content(
    tmp_path: Path,
) -> None:
    """S-6 (criterion 17): an inline-eligible image on a known OpenAI identity emits ImageContent.

    The capability layer's per-provider inline gate considers
    ``gpt-4o`` a vision-capable model. With the new unconditional
    inline path the verdict is no longer a gate, but the test still
    proves the runtime picks the inline block for the canonical
    vision-capable OpenAI identity.
    """
    _write_png(tmp_path)
    session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)
    profile = ResolvedCapabilityProfile(
        identity=MultimodalModelIdentity(provider="openai", model_id="gpt-4o"),
        verdicts={
            "image": CapabilityVerdict(
                modality="image",
                delivery=DeliveryMode.INLINE_IMAGE,
                provider="openai",
                model_id="gpt-4o",
                reason="openai supports inline image delivery",
            ),
        },
    )
    _set_profile(session, profile)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(_png_bytes())
        try:
            ws = MagicMock()
            ws.absolute_path.return_value = f.name
            result = _handle_workspace_media(
                session,
                ws,
                "smoke.png",
                max_inline_bytes=DEFAULT_MAX_INLINE_BYTES,
            )
            assert result.is_error is False
            image_blocks = [b for b in result.content if isinstance(b, ImageContent)]
            assert image_blocks, "expected an ImageContent block for the inline-eligible PNG"
            # NO warning block on the inline path.
            for block in result.content:
                assert not (
                    isinstance(block, ToolContent) and "WARNING" in block.text
                )
        finally:
            Path(f.name).unlink(missing_ok=True)


def test_inline_eligible_image_with_unknown_identity_emits_image_content(
    tmp_path: Path,
) -> None:
    """S-6 (criterion 17) / criterion 14: an unresolvable identity is treated as capable.

    An unknown provider identity on an inline-eligible PNG must
    still take the inline path -- criterion 14 collapses
    "unresolvable" into "capable" for image delivery, so the agent
    sees the image bytes and a successful, non-error result with
    NO warning block. The identity_unknown -> warn-block
    prepending is reserved for the resource_reference path.
    """
    _write_png(tmp_path)
    session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)
    identity = MultimodalModelIdentity(provider="unknown")
    profile = ResolvedCapabilityProfile(
        identity=identity,
        verdicts={
            "image": CapabilityVerdict(
                modality="image",
                delivery=DeliveryMode.RESOURCE_REFERENCE_REPLAY,
                provider="unknown",
                model_id=None,
                reason="unknown provider \u2014 defaulting to resource_reference_replay",
            ),
        },
    )
    _set_profile(session, profile)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(_png_bytes())
        try:
            ws = MagicMock()
            ws.absolute_path.return_value = f.name
            result = _handle_workspace_media(
                session,
                ws,
                "smoke.png",
                max_inline_bytes=DEFAULT_MAX_INLINE_BYTES,
            )
            assert result.is_error is False
            image_blocks = [b for b in result.content if isinstance(b, ImageContent)]
            assert image_blocks, (
                "expected an ImageContent block for the inline-eligible PNG even "
                "though the identity is unresolvable (criterion 14)"
            )
            # The identity_unknown -> warn-block prepending is reserved
            # for the resource_reference path; the inline path returns
            # the image alone, with NO warning block.
            for block in result.content:
                assert not (
                    isinstance(block, ToolContent) and "WARNING" in block.text
                )
        finally:
            Path(f.name).unlink(missing_ok=True)


def test_known_claude_identity_for_image_emits_no_warning_block(
    tmp_path: Path,
) -> None:
    """A Claude identity under an INLINE_IMAGE-capable modality returns the image with NO warning block.

    The default assumption is multimodal-present. A known provider
    on a supported modality does NOT add the warning block -- the
    warning is a signal of degradation, not a routine breadcrumb.
    """
    _write_png(tmp_path)
    session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)
    profile = ResolvedCapabilityProfile(
        identity=MultimodalModelIdentity(provider="claude", model_id="claude-opus-4-7"),
        verdicts={
            "image": CapabilityVerdict(
                modality="image",
                delivery=DeliveryMode.INLINE_IMAGE,
                provider="claude",
                model_id="claude-opus-4-7",
                reason="Claude supports inline image delivery",
            ),
        },
    )
    _set_profile(session, profile)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(_png_bytes())
        try:
            ws = MagicMock()
            ws.absolute_path.return_value = f.name
            result = _handle_workspace_media(
                session,
                ws,
                "smoke.png",
                max_inline_bytes=DEFAULT_MAX_INLINE_BYTES,
            )
            assert result.is_error is False
            # NO warning block is prepended -- every content block is an
            # image block.
            for block in result.content:
                assert not (
                    isinstance(block, ToolContent)
                    and "WARNING" in block.text
                )
            # The actual delivery is an image content block.
            image_blocks = [b for b in result.content if isinstance(b, ImageContent)]
            assert image_blocks
        finally:
            Path(f.name).unlink(missing_ok=True)
