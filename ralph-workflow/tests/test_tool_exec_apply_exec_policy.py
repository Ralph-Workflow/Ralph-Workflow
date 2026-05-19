"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

import pytest

from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
)
from ralph.mcp.tools.exec import (
    apply_exec_policy,
    parse_exec_params,
)

CUSTOM_TIMEOUT_MS = 5000
EXPECTED_TIMEOUT_SECONDS = 2.5


class TestApplyExecPolicy:
    def test_allowed_command_passes(self) -> None:
        apply_exec_policy("ls", ["-la"])

    def test_denied_command_raises(self) -> None:
        with pytest.raises(CapabilityDeniedError):
            apply_exec_policy("git", ["status"])

    def test_embedded_blacklisted_command_is_denied_after_parse(self) -> None:
        parsed = parse_exec_params({"command": "sudo ls"})
        with pytest.raises(CapabilityDeniedError):
            apply_exec_policy(parsed.command, parsed.args)
