"""Tests for mcp.toml Pydantic models."""

from __future__ import annotations

import importlib

import pytest
from pydantic import ValidationError

from ralph.config.mcp_models import (
    McpConfig,
    McpServerSpec,
    MediaConfig,
    WebSearchBackendSpec,
    WebSearchConfig,
)

RALPH_RESERVED_NAME = "ralph"

DEFAULT_MAX_INLINE_BYTES = 5_242_880  # 5 MiB
_TEN_MIB = 10_485_760  # 10 MiB - used in tests


def test_mcp_server_spec_rejects_reserved_name() -> None:
    with pytest.raises(ValidationError, match=RALPH_RESERVED_NAME):
        McpServerSpec(name=RALPH_RESERVED_NAME, transport="stdio", command="npx")


def test_mcp_server_spec_rejects_double_underscore_name() -> None:
    with pytest.raises(ValidationError, match="__"):
        McpServerSpec(name="github__enterprise", transport="stdio", command="npx")


def test_mcp_server_spec_rejects_invalid_name_pattern() -> None:
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        McpServerSpec(name="GitHub", transport="stdio", command="npx")


def test_mcp_server_spec_http_requires_url_and_forbids_stdio_fields() -> None:
    with pytest.raises(ValidationError, match="url"):
        McpServerSpec(name="github", transport="http")

    with pytest.raises(ValidationError, match="command"):
        McpServerSpec(name="github", transport="http", url="https://example.com", command="npx")

    with pytest.raises(ValidationError, match="args"):
        McpServerSpec(
            name="github",
            transport="http",
            url="https://example.com",
            args=["--foo"],
        )


def test_mcp_server_spec_stdio_requires_command_and_forbids_url() -> None:
    with pytest.raises(ValidationError, match="command"):
        McpServerSpec(name="github", transport="stdio")

    with pytest.raises(ValidationError, match="url"):
        McpServerSpec(name="github", transport="stdio", command="npx", url="https://example.com")


def test_web_search_backend_spec_enforces_key_xor_env_for_keyed_backends() -> None:
    with pytest.raises(ValidationError, match="api_key"):
        WebSearchBackendSpec(backend="tavily")

    with pytest.raises(ValidationError, match="api_key"):
        WebSearchBackendSpec(backend="exa", api_key="secret", api_key_env="EXA_API_KEY")

    spec = WebSearchBackendSpec(backend="tavily", api_key_env="TAVILY_API_KEY")
    assert spec.api_key is None
    assert spec.api_key_env == "TAVILY_API_KEY"


def test_web_search_backend_spec_allows_keyless_ddgs_and_searxng() -> None:
    ddgs = WebSearchBackendSpec(backend="ddgs")
    searxng = WebSearchBackendSpec(backend="searxng", url="https://search.example")

    assert ddgs.api_key is None
    assert searxng.url == "https://search.example"


def test_web_search_backend_spec_requires_url_for_searxng() -> None:
    with pytest.raises(ValidationError, match="searxng"):
        WebSearchBackendSpec(backend="searxng")


def test_web_search_config_defaults_and_mcp_config_roundtrip() -> None:
    config = McpConfig(
        mcp_servers={
            "github": McpServerSpec(name="github", transport="stdio", command="npx"),
        },
        web_search=WebSearchConfig(
            backends={
                "ddgs": WebSearchBackendSpec(backend="ddgs"),
            }
        ),
    )

    assert config.web_search.enabled is True
    assert config.web_search.backend == "ddgs"
    assert config.mcp_servers["github"].chains is None

    roundtrip = McpConfig.model_validate(config.model_dump())
    assert roundtrip == config


def test_model_module_exports_all() -> None:
    module = importlib.import_module("ralph.config.mcp_models")
    assert {"McpServerSpec", "WebSearchBackendSpec", "WebSearchConfig", "McpConfig"}.issubset(
        set(module.__all__)
    )


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
