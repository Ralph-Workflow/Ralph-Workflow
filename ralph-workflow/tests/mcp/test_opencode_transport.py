"""Tests for ralph/mcp/transport/opencode.py upstream handling."""

from __future__ import annotations

import json

from ralph.mcp.transport.opencode import build_opencode_provider_config

_OPERATOR_CONFIG_WITH_STALE_RALPH = json.dumps(
    {
        "mcp": {
            "ralph": {"type": "remote", "url": "http://stale.example/mcp", "enabled": True},
            "github": {"type": "remote", "url": "https://github.example/mcp", "enabled": True},
        }
    }
)


def test_opencode_transport_regression_stale_ralph_entry_is_dropped_not_rejected() -> None:
    """Restricted mode replaces a leftover `ralph` entry instead of rejecting the config."""
    config_text, upstreams = build_opencode_provider_config(
        _OPERATOR_CONFIG_WITH_STALE_RALPH, "http://127.0.0.1:9999/mcp"
    )

    servers = json.loads(config_text)["mcp"]
    assert list(servers) == ["ralph"]
    assert servers["ralph"]["url"] == "http://127.0.0.1:9999/mcp"
    assert [server.name for server in upstreams] == ["github"]


def test_opencode_transport_regression_stale_ralph_entry_is_dropped_in_unsafe_mode() -> None:
    """Unsafe mode keeps the operator's other servers and still wins the `ralph` name."""
    config_text, upstreams = build_opencode_provider_config(
        _OPERATOR_CONFIG_WITH_STALE_RALPH, "http://127.0.0.1:9999/mcp", unsafe_mode=True
    )

    servers = json.loads(config_text)["mcp"]
    assert sorted(servers) == ["github", "ralph"]
    assert servers["ralph"]["url"] == "http://127.0.0.1:9999/mcp"
    assert servers["github"]["url"] == "https://github.example/mcp"
    assert [server.name for server in upstreams] == ["github"]
