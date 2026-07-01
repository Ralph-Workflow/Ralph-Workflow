"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from ralph.mcp.multimodal.artifacts import (
    ResourceReferenceContent,
)
from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    ImageContent,
    ToolContent,
)
from ralph.mcp.tools.workspace import (
    WORKSPACE_READ_CAPABILITY,
    handle_read_file,
    handle_read_image,
)
from tests.mock_session import MockSession
from tests.mock_session_with_manifest import MockSessionWithManifest

pytestmark = pytest.mark.subprocess_e2e

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
        assert "Unsupported image format" in cast("ToolContent", result.content[0]).text
        assert ".pdf" in cast("ToolContent", result.content[0]).text

    def test_returns_error_for_missing_file(self) -> None:
        ws = MagicMock()
        ws.absolute_path.return_value = "/tmp/nonexistent.png"

        # MockSessionWithManifest is required for capability-aware delivery path
        result = handle_read_image(
            MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(provider="claude"),
            ),
            ws,
            {"path": "nonexistent.png"},
        )
        assert result.is_error is True
        assert "Failed to read" in cast("ToolContent", result.content[0]).text

    def test_delivers_via_resource_reference_when_inline_too_large(self) -> None:
        """When inline image is too large, falls back to resource-reference delivery.

        This tests that handle_read_image (as a compatibility alias over
        _handle_workspace_media) properly routes oversized images through the
        resource-reference path when inline delivery is not possible.
        """
        ws = MagicMock()

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x00" * (DEFAULT_MAX_INLINE_BYTES + 1))
            temp_path = f.name

        try:
            ws.absolute_path.return_value = temp_path

            # MockSessionWithManifest with INLINE_IMAGE support but file exceeds limit
            result = handle_read_image(
                MockSessionWithManifest(
                    MEDIA_READ_CAPABILITY,
                    model_identity=MultimodalModelIdentity(provider="claude"),
                ),
                ws,
                {"path": "large.png"},
                max_inline_bytes=DEFAULT_MAX_INLINE_BYTES,
            )
            # With INLINE_IMAGE support but oversized file, falls back to resource-reference
            assert result.is_error is False
            content = result.content[0]
            # Should be a resource reference, not an inline image
            assert isinstance(content, ResourceReferenceContent), (
                f"Expected resource-reference block, got {type(content).__name__}"
            )
            assert content.uri.startswith("ralph://media/"), (
                f"Expected ralph://media/ URI, got: {content.uri}"
            )
        finally:
            Path(temp_path).unlink()

    def test_returns_image_content_block_on_success(self) -> None:
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
            "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(png_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path

            # MockSessionWithManifest with INLINE_IMAGE support (claude model)
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
        finally:
            Path(temp_path).unlink()

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
        assert cast("ToolContent", result.content[0]).text == "hello world"
        assert not isinstance(result.content[0], ImageContent)
