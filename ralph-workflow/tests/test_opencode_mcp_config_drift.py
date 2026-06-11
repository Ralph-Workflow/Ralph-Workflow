"""Anti-drift guards for the OpenCode MCP client config.

The OpenCode MCP CLIENT request timeout must exceed the longest server-side tool
execution, or the client gives up with `-32001 Request timed out` before the server
finishes, producing the retry storm seen in production.

CRITICAL: OpenCode IGNORES the documented per-server ``mcp.<server>.timeout`` field
and only honors the global ``experimental.mcp_timeout`` (opencode issues #8701/#8121).
The effective timeout MUST therefore be set via ``experimental.mcp_timeout``.
"""

from __future__ import annotations

import json

from ralph.mcp.transport.opencode import build_opencode_provider_config
from ralph.timeout_defaults import EXEC_MAX_TIMEOUT_MS


def _config(existing: str | None = None, *, unsafe_mode: bool = False) -> dict:
    config_text, _ = build_opencode_provider_config(
        existing, "http://127.0.0.1:9999/mcp", unsafe_mode=unsafe_mode
    )
    config = json.loads(config_text)
    assert isinstance(config, dict)
    return config


def test_effective_mcp_timeout_exceeds_max_exec_tool_timeout() -> None:
    # experimental.mcp_timeout is the field OpenCode actually honors.
    experimental = _config()["experimental"]
    assert isinstance(experimental, dict)
    effective = experimental["mcp_timeout"]
    assert isinstance(effective, int)
    assert effective > EXEC_MAX_TIMEOUT_MS, (
        f"experimental.mcp_timeout {effective} must exceed the max exec timeout "
        f"{EXEC_MAX_TIMEOUT_MS}, or a max-length exec re-triggers the -32001 storm."
    )


def test_per_server_timeout_also_set_for_forward_compat() -> None:
    # Kept (harmless) for if/when OpenCode honors the documented per-server field.
    server = _config()["mcp"]["ralph"]
    assert isinstance(server, dict)
    assert server["timeout"] > EXEC_MAX_TIMEOUT_MS


def test_unsafe_mode_merges_existing_mcp_servers() -> None:
    """unsafe_mode=True preserves any pre-existing OpenCode MCP servers."""
    existing = json.dumps(
        {
            "mcp": {
                "existing-server": {
                    "type": "remote",
                    "url": "http://other.example/mcp",
                    "enabled": True,
                }
            }
        }
    )

    config = _config(existing, unsafe_mode=True)
    mcp = config["mcp"]
    assert isinstance(mcp, dict)
    assert "existing-server" in mcp
    assert "ralph" in mcp
    ralph = mcp["ralph"]
    assert isinstance(ralph, dict)
    assert ralph["url"] == "http://127.0.0.1:9999/mcp"
    assert ralph["timeout"] > EXEC_MAX_TIMEOUT_MS


def test_unsafe_mode_false_replaces_existing_mcp_servers() -> None:
    """unsafe_mode=False (default) replaces existing servers with Ralph-only config."""
    existing = json.dumps(
        {
            "mcp": {
                "existing-server": {
                    "type": "remote",
                    "url": "http://other.example/mcp",
                    "enabled": True,
                }
            }
        }
    )

    config = _config(existing, unsafe_mode=False)
    mcp = config["mcp"]
    assert isinstance(mcp, dict)
    assert list(mcp.keys()) == ["ralph"]
    ralph = mcp["ralph"]
    assert isinstance(ralph, dict)
    assert ralph["timeout"] > EXEC_MAX_TIMEOUT_MS


def test_unsafe_mode_overwrites_stale_ralph_entry() -> None:
    """unsafe_mode=True still lets Ralph win on name collision."""
    existing = json.dumps(
        {
            "mcp": {
                "ralph": {
                    "type": "remote",
                    "url": "http://old.example/mcp",
                    "enabled": True,
                }
            }
        }
    )

    config = _config(existing, unsafe_mode=True)
    mcp = config["mcp"]
    assert isinstance(mcp, dict)
    ralph = mcp["ralph"]
    assert isinstance(ralph, dict)
    assert ralph["url"] == "http://127.0.0.1:9999/mcp"
