"""Tests for the multimodal graceful-degradation warning seam (S-7 / criterion 3).

Per ``.agent/PRODUCT_CRITERIA.md`` (criterion 3): "Treat a multimodal
model as ASSUMED to be present. If it turns out to be missing,
DEGRADE GRACEFULLY WITH A WARNING -- but the default assumption is
always that multimodal works."

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

    The production path stores the profile on the session at
    :class:`CoordinationSession`-level; the mock session exposes
    the same attribute so the production helper can be reused.
    """
    session.capability_profile = profile


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


def test_unknown_identity_for_image_emits_warning_then_resource_reference(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """UNKNOWN_IDENTITY degrades with a warning, then emits a resource-reference block.

    Criterion 3: default assumption is multimodal-present; the
    only signal of degradation is the warning block -- the artifact
    is still delivered as a replayable resource reference so the
    agent can proceed.
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

    class _Ws:
        def absolute_path(self, relpath: str) -> str:
            return str(tmp_path / relpath)

    ws = _Ws()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(_png_bytes())
        try:
            ws = MagicMock()
            ws.absolute_path.return_value = f.name
            # The smoke harness fixture path is overridden to the
            # temp file so the bytes are inside the inline cap.
            result = _handle_workspace_media(
                session,
                ws,
                "smoke.png",
                max_inline_bytes=DEFAULT_MAX_INLINE_BYTES,
            )
            assert result.is_error is False
            # First content block is the warning.
            first_text = result.content[0].text
            assert "WARNING" in first_text
            assert "unknown" in first_text
            assert "image" in first_text
            # A subsequent block is the resource reference.
            ref_present = any(
                getattr(block, "type", None) == "resource_reference"
                or "resource_reference" in str(getattr(block, "type", ""))
                for block in result.content
            )
            assert ref_present
        finally:
            Path(f.name).unlink(missing_ok=True)


def test_unsupported_verdict_emits_warning_then_error_block(
    tmp_path: Path,
) -> None:
    """AN UNSUPPORTED verdict still emits a warning block, then the existing error text.

    The error is preserved so the agent sees the structured failure
    -- the warning block is an ADDITIONAL operator-visible signal
    per criterion 3 ("DEGRADE GRACEFULLY WITH A WARNING").
    """
    _write_png(tmp_path)
    session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)
    profile = ResolvedCapabilityProfile(
        identity=MultimodalModelIdentity(provider="claude"),
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
            result = _handle_workspace_media(
                session,
                ws,
                "smoke.png",
                max_inline_bytes=DEFAULT_MAX_INLINE_BYTES,
            )
            assert result.is_error is True
            # A warning block comes before the error text.
            assert "WARNING" in result.content[0].text
            assert "claude" in result.content[0].text
            # The second content block carries the existing structured failure text.
            assert any(
                isinstance(block, ToolContent)
                and "is not supported" in block.text
                for block in result.content
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
