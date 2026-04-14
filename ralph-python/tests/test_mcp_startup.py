"""Tests for the MCP startup port."""

from __future__ import annotations

import datetime
import os

import pytest

from ralph.mcp.capability_mapping import AccessMode, SessionDrain


def test_access_mode_for_drain_planning_is_read_only() -> None:
    from ralph.mcp import startup

    assert startup.access_mode_for_drain(SessionDrain.PLANNING) is AccessMode.READ_ONLY


def test_parse_tcp_endpoint_requires_tcp_scheme() -> None:
    from ralph.mcp import startup

    with pytest.raises(ValueError, match="tcp://"):
        startup.parse_tcp_endpoint("127.0.0.1:1234")


def test_mcp_preflight_timeout_from_env_defaults_to_30_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.mcp import startup

    monkeypatch.delenv("RALPH_MCP_PREFLIGHT_TIMEOUT_MS", raising=False)
    expected = datetime.timedelta(milliseconds=30_000)
    assert startup.mcp_preflight_timeout_from_env() == expected
