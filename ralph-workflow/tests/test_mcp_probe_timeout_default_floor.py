"""The MCP probe timeout default must be 6 s, not 5 s."""

from __future__ import annotations

from datetime import timedelta

from ralph.mcp.protocol.startup import mcp_probe_timeout_from_env


def test_mcp_probe_timeout_default_is_six_seconds() -> None:
    assert mcp_probe_timeout_from_env({}) == timedelta(milliseconds=6_000)


def test_mcp_probe_timeout_respects_env_override() -> None:
    assert mcp_probe_timeout_from_env(
        {"RALPH_MCP_PROBE_TIMEOUT_MS": "12345"}
    ) == timedelta(milliseconds=12_345)
