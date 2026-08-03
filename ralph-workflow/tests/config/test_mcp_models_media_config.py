"""Tests for mcp.toml Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ralph.config.mcp_models import (
    MediaConfig,
)

RALPH_RESERVED_NAME = "ralph"

DEFAULT_MAX_INLINE_BYTES = 5_242_880  # 5 MiB
_TEN_MIB = 10_485_760  # 10 MiB - used in tests


# =============================================================================
# MediaConfig tests (Task 4)
# =============================================================================


class TestMediaConfig:
    """Tests for MediaConfig model (Task 4)."""

    def test_media_config_defaults(self) -> None:
        """media config defaults to enabled with sane max_inline_bytes."""
        config = MediaConfig()
        assert config.enabled is True
        assert config.max_inline_bytes == DEFAULT_MAX_INLINE_BYTES

    def test_media_config_enabled_true(self) -> None:
        """MediaConfig can be enabled explicitly."""
        config = MediaConfig(enabled=True)
        assert config.enabled is True
        assert config.max_inline_bytes == DEFAULT_MAX_INLINE_BYTES

    def test_media_config_custom_max_inline_bytes(self) -> None:
        """MediaConfig accepts custom max_inline_bytes."""
        config = MediaConfig(enabled=True, max_inline_bytes=_TEN_MIB)
        assert config.enabled is True
        assert config.max_inline_bytes == _TEN_MIB

    def test_media_config_max_inline_bytes_must_be_positive(self) -> None:
        """MediaConfig rejects non-positive max_inline_bytes."""
        with pytest.raises(ValidationError, match="greater than 0"):
            MediaConfig(max_inline_bytes=0)
        with pytest.raises(ValidationError, match="greater than 0"):
            MediaConfig(max_inline_bytes=-1)

    def test_media_config_is_frozen(self) -> None:
        """MediaConfig is immutable (frozen=True)."""
        config = MediaConfig()
        with pytest.raises(ValidationError):
            config.enabled = True

    def test_media_config_docstring_describes_broad_multimodal_not_image_only(self) -> None:
        """MediaConfig.__doc__ must not describe support as image-only.

        The docstring is the first contract surface an operator reads when
        configuring mcp.toml. It must reflect the actual broad multimodal
        default-on behavior rather than the stale image-only framing.
        """
        doc = MediaConfig.__doc__ or ""
        assert "image support" not in doc.lower(), (
            "MediaConfig.__doc__ must not say 'image support' — "
            "Ralph supports broad multimodal (images, PDFs, audio, video, documents), "
            f"not image-only.\nActual docstring: {doc!r}"
        )
        assert "multimodal" in doc.lower(), (
            f"MediaConfig.__doc__ must mention multimodal support. Actual docstring: {doc!r}"
        )
