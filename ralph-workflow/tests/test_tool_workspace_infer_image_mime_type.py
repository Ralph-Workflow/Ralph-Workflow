"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

from ralph.mcp.tools.workspace import (
    infer_image_mime_type,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestInferImageMimeType:
    def test_png(self) -> None:
        assert infer_image_mime_type("image.png") == "image/png"

    def test_jpg(self) -> None:
        assert infer_image_mime_type("image.jpg") == "image/jpeg"

    def test_jpeg(self) -> None:
        assert infer_image_mime_type("image.jpeg") == "image/jpeg"

    def test_gif(self) -> None:
        assert infer_image_mime_type("image.gif") == "image/gif"

    def test_webp(self) -> None:
        assert infer_image_mime_type("image.webp") == "image/webp"

    def test_unknown_suffix_returns_none(self) -> None:
        assert infer_image_mime_type("document.pdf") is None
        assert infer_image_mime_type("video.mp4") is None
        assert infer_image_mime_type("unknown.xyz") is None

    def test_empty_suffix_returns_none(self) -> None:
        assert infer_image_mime_type("noextension") is None
