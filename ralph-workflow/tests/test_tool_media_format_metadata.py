from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ralph.mcp.multimodal.artifacts import ResourceReferenceContent
from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
from ralph.mcp.tools.coordination import ImageContent
from ralph.mcp.tools.workspace._media_handlers import (
    handle_read_image,
    handle_read_media,
)
from ralph.workspace.fs import FsWorkspace
from tests.mock_session import MockSession
from tests.mock_session_with_manifest import MockSessionWithManifest

MEDIA_READ_CAPABILITY = "media.read"

pytestmark = pytest.mark.timeout_seconds(5)


_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDAT"
    b"\x78\x9c\x62\x00\x01\x00\x00\x05\x00\x01"
    b"\x0d\x0a\x2d\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _write_png(tmp_path: Path, name: str = "tiny.png") -> Path:
    file_path = tmp_path / name
    file_path.write_bytes(_PNG_BYTES)
    return file_path


class TestReadImageMetadata:
    def test_read_image_metadata_returns_bounded_envelope(self, tmp_path: Path) -> None:
        _write_png(tmp_path)
        ws = FsWorkspace(tmp_path)

        result = handle_read_image(
            MockSession(MEDIA_READ_CAPABILITY),
            ws,
            {"path": "tiny.png", "format": "metadata"},
        )

        assert result.is_error is False
        envelope = json.loads(result.content[0].text)
        assert envelope["format"] == "metadata"
        assert envelope["media_kind"] == "image"
        assert envelope["mime_type"] == "image/png"
        assert envelope["size_bytes"] == len(_PNG_BYTES)
        assert envelope["sha256"] == hashlib.sha256(_PNG_BYTES).hexdigest()
        assert envelope["width"] == 1
        assert envelope["height"] == 1
        assert envelope["resource_handle"] is None
        assert envelope["inline_only"] is True
        assert envelope["truncated"] is False
        assert envelope["bytes_out"] > 0
        assert not any(isinstance(c, ImageContent) for c in result.content)

    def test_read_image_inline_default_unchanged(self, tmp_path: Path) -> None:
        _write_png(tmp_path)
        ws = FsWorkspace(tmp_path)

        result = handle_read_image(
            MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(provider="claude"),
            ),
            ws,
            {"path": "tiny.png"},
        )

        assert result.is_error is False
        assert any(isinstance(c, ImageContent) for c in result.content)

    def test_ccs_read_image_above_inline_cap_returns_text_replay_handle(
        self, tmp_path: Path
    ) -> None:
        _write_png(tmp_path)
        ws = FsWorkspace(tmp_path)

        result = handle_read_image(
            MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(provider="ccs"),
            ),
            ws,
            {"path": "tiny.png"},
            max_inline_bytes=1,
        )

        assert result.is_error is False
        assert len(result.content) == 1
        assert result.content[0].type == "text"
        assert result.content[0].text.startswith("Replay handle: ralph://media/")

    def test_ccs_metadata_registers_a_replay_handle(self, tmp_path: Path) -> None:
        _write_png(tmp_path)
        result = handle_read_image(
            MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(provider="ccs"),
            ),
            FsWorkspace(tmp_path),
            {"path": "tiny.png", "format": "metadata"},
            max_inline_bytes=1,
        )

        envelope = json.loads(result.content[0].text)

        assert envelope["resource_handle"].startswith("ralph://media/")
        assert envelope["inline_only"] is False

    def test_read_image_metadata_invalid_format_raises(self, tmp_path: Path) -> None:
        _write_png(tmp_path)
        ws = FsWorkspace(tmp_path)

        from ralph.mcp.tools.coordination import InvalidParamsError

        with pytest.raises(InvalidParamsError, match="Invalid format"):
            handle_read_image(
                MockSession(MEDIA_READ_CAPABILITY),
                ws,
                {"path": "tiny.png", "format": "bogus"},
            )


class TestReadMediaMetadata:
    def test_read_media_metadata_returns_bounded_envelope_for_pdf(self, tmp_path: Path) -> None:
        pdf_path = tmp_path / "report.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake pdf content")
        ws = FsWorkspace(tmp_path)

        result = handle_read_media(
            MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(provider="unknown"),
            ),
            ws,
            {"path": "report.pdf", "format": "metadata"},
        )

        assert result.is_error is False
        envelope = json.loads(result.content[0].text)
        assert envelope["format"] == "metadata"
        assert envelope["media_kind"] == "pdf"
        assert envelope["mime_type"] == "application/pdf"
        assert envelope["size_bytes"] == len(b"%PDF-1.4 fake pdf content")
        assert envelope["sha256"] == hashlib.sha256(b"%PDF-1.4 fake pdf content").hexdigest()
        assert envelope["resource_handle"] is not None
        assert str(envelope["resource_handle"]).startswith("ralph://media/")
        assert envelope["inline_only"] is False
        assert envelope["bytes_out"] > 0
        assert not any(isinstance(c, ResourceReferenceContent) for c in result.content)

    def test_read_media_metadata_returns_bounded_envelope_for_image(self, tmp_path: Path) -> None:
        _write_png(tmp_path, name="shot.png")
        ws = FsWorkspace(tmp_path)

        result = handle_read_media(
            MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(provider="claude"),
            ),
            ws,
            {"path": "shot.png", "format": "metadata"},
        )

        assert result.is_error is False
        envelope = json.loads(result.content[0].text)
        assert envelope["format"] == "metadata"
        assert envelope["media_kind"] == "image"
        assert envelope["mime_type"] == "image/png"
        assert envelope["size_bytes"] == len(_PNG_BYTES)
        assert envelope["sha256"] == hashlib.sha256(_PNG_BYTES).hexdigest()
        assert envelope["resource_handle"] is None
        assert envelope["inline_only"] is True
        assert envelope["width"] == 1
        assert envelope["height"] == 1

    def test_read_media_inline_default_unchanged_for_pdf(self, tmp_path: Path) -> None:
        pdf_path = tmp_path / "report.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake pdf content")
        ws = FsWorkspace(tmp_path)

        result = handle_read_media(
            MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(provider="unknown"),
            ),
            ws,
            {"path": "report.pdf"},
        )

        assert result.is_error is False
        legacy_blocks = [
            c
            for c in result.content
            if isinstance(c, ResourceReferenceContent)
            or c.__class__.__name__
            in {"PdfContent", "AudioContent", "VideoContent", "DocumentContent", "ImageContent"}
        ]
        assert legacy_blocks, (
            f"inline mode must emit a legacy typed/resource-reference block; got {result.content!r}"
        )
        for c in result.content:
            text = getattr(c, "text", None)
            if text is not None:
                assert '"format": "metadata"' not in text, (
                    f"inline mode must not emit the metadata envelope: {text!r}"
                )

    def test_read_media_metadata_invalid_format_raises(self, tmp_path: Path) -> None:
        pdf_path = tmp_path / "report.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake pdf content")
        ws = FsWorkspace(tmp_path)

        from ralph.mcp.tools.coordination import InvalidParamsError

        with pytest.raises(InvalidParamsError, match="Invalid format"):
            handle_read_media(
                MockSessionWithManifest(
                    MEDIA_READ_CAPABILITY,
                    model_identity=MultimodalModelIdentity(provider="claude"),
                ),
                ws,
                {"path": "report.pdf", "format": "bogus"},
            )
