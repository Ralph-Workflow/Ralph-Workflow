from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.mcp.multimodal.artifacts import (
    ResourceReferenceContent,
)
from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    ImageContent,
)
from ralph.mcp.tools.workspace import (
    WORKSPACE_READ_CAPABILITY,
    handle_read_file,
    handle_read_image,
)
from ralph.workspace.fs import FsWorkspace
from tests.mock_session import MockSession
from tests.mock_session_with_manifest import MockSessionWithManifest

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleReadImage:
    def test_requires_media_read_capability(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError) as exc_info:
            handle_read_image(MockSession(), ws, {"path": "image.png"})

        assert "media.read" in str(exc_info.value)

    def test_returns_error_for_unsupported_format(self) -> None:
        ws = MagicMock()

        result = handle_read_image(
            MockSession(MEDIA_READ_CAPABILITY),
            ws,
            {"path": "document.pdf"},
        )

        assert result.is_error is True
        assert "Unsupported image format" in result.content[0].text
        assert ".pdf" in result.content[0].text

    def test_returns_error_for_missing_file(self) -> None:
        ws = MagicMock()
        ws.absolute_path.return_value = "/tmp/nonexistent.png"

        result = handle_read_image(
            MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(provider="claude"),
            ),
            ws,
            {"path": "nonexistent.png"},
        )
        assert result.is_error is True
        assert "Failed to read" in result.content[0].text

    def test_delivers_via_resource_reference_when_inline_too_large(self, tmp_path: Path) -> None:
        media_file = tmp_path / "large.png"
        media_file.write_bytes(b"\x89PNG" + b"\x00" * 16)
        ws = FsWorkspace(tmp_path)

        result = handle_read_image(
            MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(provider="claude"),
            ),
            ws,
            {"path": "large.png"},
            max_inline_bytes=10,
        )
        assert result.is_error is False
        content = result.content[0]
        assert isinstance(content, ResourceReferenceContent), (
            f"Expected resource-reference block, got {type(content).__name__}"
        )
        assert content.uri.startswith("ralph://media/"), (
            f"Expected ralph://media/ URI, got: {content.uri}"
        )

    def test_returns_image_content_block_on_success(self, tmp_path: Path) -> None:
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
            "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )

        media_file = tmp_path / "test.png"
        media_file.write_bytes(png_bytes)

        ws = MagicMock()
        ws.absolute_path.return_value = str(media_file)

        result = handle_read_image(
            MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(provider="claude"),
            ),
            ws,
            {"path": "test.png"},
        )
        assert result.is_error is False
        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, ImageContent)
        assert content.type == "image"
        assert content.mime_type == "image/png"

    def test_read_file_unchanged_text_only(self) -> None:
        ws = MagicMock()
        ws.read.return_value = "hello world"

        result = handle_read_file(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": "hello.txt"},
        )

        assert result.is_error is False
        assert hasattr(result.content[0], "text")
        assert result.content[0].text == "hello world"
        assert not isinstance(result.content[0], ImageContent)
