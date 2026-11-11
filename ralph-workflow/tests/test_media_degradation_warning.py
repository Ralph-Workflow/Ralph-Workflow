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
    target = tmp_path / name
    target.write_bytes(b"%PDF-1.4\n%fake-test-pdf\n%%EOF\n")
    return target


def test_warning_block_names_provider_modality_and_reason() -> None:
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
    assert "ASSUMED" in text or "assumed" in text


def test_warning_block_names_provider_model_id_and_delivery_mode() -> None:
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
    first_text = result.content[0].text
    assert "WARNING" in first_text
    assert "unknown" in first_text
    assert "pdf" in first_text
    ref_present = any(
        getattr(block, "type", None) == "resource_reference"
        or "resource_reference" in str(getattr(block, "type", ""))
        for block in result.content
    )
    assert ref_present


def test_unsupported_verdict_emits_warning_then_resource_reference_fallback(
    tmp_path: Path,
) -> None:
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
            assert result.is_error is False
            assert len(result.content) >= 2
            first_block = result.content[0]
            assert isinstance(first_block, ToolContent)
            first_text = first_block.text
            assert "WARNING" in first_text
            assert "claude" in first_text
            assert "claude-opus-4-7" in first_text
            assert "unsupported" in first_text
            fallback = result.content[1]
            assert isinstance(fallback, ResourceReferenceContent)
            assert fallback.uri
            assert fallback.mime_type == "image/png"
            assert fallback.modality == "image"
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
            for block in result.content:
                assert not (isinstance(block, ToolContent) and "WARNING" in block.text)
        finally:
            Path(f.name).unlink(missing_ok=True)


def test_inline_eligible_image_with_unknown_identity_emits_image_content(
    tmp_path: Path,
) -> None:
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
            for block in result.content:
                assert not (isinstance(block, ToolContent) and "WARNING" in block.text)
        finally:
            Path(f.name).unlink(missing_ok=True)


def test_known_claude_identity_for_image_emits_no_warning_block(
    tmp_path: Path,
) -> None:
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
            for block in result.content:
                assert not (isinstance(block, ToolContent) and "WARNING" in block.text)
            image_blocks = [b for b in result.content if isinstance(b, ImageContent)]
            assert image_blocks
        finally:
            Path(f.name).unlink(missing_ok=True)
