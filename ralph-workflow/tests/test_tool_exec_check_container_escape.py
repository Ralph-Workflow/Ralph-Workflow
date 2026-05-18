"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

from ralph.mcp.tools.exec import (
    check_container_escape,
)

CUSTOM_TIMEOUT_MS = 5000
EXPECTED_TIMEOUT_SECONDS = 2.5


class TestCheckContainerEscape:
    def test_docker_is_blacklisted(self) -> None:
        reason = check_container_escape("docker", ["ps"])
        assert reason is not None
        assert "docker" in reason.lower()

    def test_podman_is_blacklisted(self) -> None:
        reason = check_container_escape("podman", ["images"])
        assert reason is not None

    def test_allowed_command_returns_none(self) -> None:
        reason = check_container_escape("ps", ["aux"])
        assert reason is None
