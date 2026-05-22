"""Tests for ralph.skills._docs_mcp_probe URL validation."""

from ralph.skills._docs_mcp_probe import (
    SUPPORTED_DOCS_MCP_PATHS,
    is_supported_docs_mcp_url,
)


class TestIsSupportedDocsMcpUrl:
    def test_accepts_mcp_path(self) -> None:
        assert is_supported_docs_mcp_url("http://localhost:6280/mcp") is True

    def test_accepts_sse_path(self) -> None:
        assert is_supported_docs_mcp_url("http://localhost:6280/sse") is True

    def test_rejects_https(self) -> None:
        assert is_supported_docs_mcp_url("https://localhost:6280/mcp") is False

    def test_rejects_different_port(self) -> None:
        assert is_supported_docs_mcp_url("http://localhost:9999/mcp") is False

    def test_rejects_different_host(self) -> None:
        assert is_supported_docs_mcp_url("http://127.0.0.1:6280/mcp") is False

    def test_rejects_unsupported_path(self) -> None:
        assert is_supported_docs_mcp_url("http://localhost:6280/other") is False

    def test_rejects_empty_path(self) -> None:
        assert is_supported_docs_mcp_url("http://localhost:6280/") is False

    def test_supported_paths_contains_mcp_and_sse(self) -> None:
        assert "/mcp" in SUPPORTED_DOCS_MCP_PATHS
        assert "/sse" in SUPPORTED_DOCS_MCP_PATHS
        assert len(SUPPORTED_DOCS_MCP_PATHS) == 2
