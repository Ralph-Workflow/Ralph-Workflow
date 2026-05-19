"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

from ralph.mcp.tools.exec import (
    check_network_exfiltration,
)

CUSTOM_TIMEOUT_MS = 5000
EXPECTED_TIMEOUT_SECONDS = 2.5


class TestCheckNetworkExfiltration:
    def test_curl_to_external_url_is_blacklisted(self) -> None:
        reason = check_network_exfiltration("curl", ["https://evil.com"])
        assert reason is not None
        assert "curl" in reason.lower()

    def test_wget_to_external_url_is_blacklisted(self) -> None:
        reason = check_network_exfiltration("wget", ["http://evil.com/file"])
        assert reason is not None

    def test_curl_to_localhost_is_allowed(self) -> None:
        reason = check_network_exfiltration("curl", ["http://localhost:8080"])
        assert reason is None

    def test_curl_to_127_0_0_1_is_allowed(self) -> None:
        reason = check_network_exfiltration("curl", ["http://127.0.0.1:8080"])
        assert reason is None

    def test_curl_without_url_flag_is_allowed(self) -> None:
        reason = check_network_exfiltration("curl", ["--version"])
        assert reason is None

    def test_nc_is_blacklisted(self) -> None:
        reason = check_network_exfiltration("nc", ["-l", "8080"])
        assert reason is not None

    def test_ssh_to_remote_is_blacklisted(self) -> None:
        reason = check_network_exfiltration("ssh", ["user@host"])
        assert reason is not None

    def test_scp_to_remote_is_blacklisted(self) -> None:
        reason = check_network_exfiltration("scp", ["file.txt", "user@host:/path"])
        assert reason is not None
