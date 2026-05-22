"""Tests for ralph.skills._docs_mcp_probe probe_docs_mcp function."""

from unittest.mock import patch

from ralph.skills._docs_mcp_probe import probe_docs_mcp


class TestProbeDocsMcp:
    def test_returns_false_for_unsupported_url(self) -> None:
        # probe_docs_mcp returns False without making HTTP call for non-matching URL
        with patch("httpx.get") as mock_get:
            result = probe_docs_mcp("http://localhost:9999/mcp")
            assert result is False
            mock_get.assert_not_called()

    def test_returns_true_when_http_200(self) -> None:
        with patch("httpx.get") as mock_get:
            mock_response = type("obj", (object,), {"status_code": 200})()
            mock_get.return_value = mock_response
            result = probe_docs_mcp("http://localhost:6280/mcp")
            assert result is True

    def test_returns_false_when_http_404(self) -> None:
        with patch("httpx.get") as mock_get:
            mock_response = type("obj", (object,), {"status_code": 404})()
            mock_get.return_value = mock_response
            result = probe_docs_mcp("http://localhost:6280/mcp")
            assert result is False

    def test_returns_false_on_connection_error(self) -> None:
        with patch("httpx.get") as mock_get:
            mock_get.side_effect = Exception("connection refused")
            result = probe_docs_mcp("http://localhost:6280/mcp")
            assert result is False

    def test_respects_timeout_parameter(self) -> None:
        with patch("httpx.get") as mock_get:
            mock_response = type("obj", (object,), {"status_code": 200})()
            mock_get.return_value = mock_response
            probe_docs_mcp("http://localhost:6280/mcp", timeout=5.0)
            call_args = mock_get.call_args
            assert call_args[1]["timeout"] == 5.0
