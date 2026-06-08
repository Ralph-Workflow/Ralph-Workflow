"""Anti-drift guards for the OpenCode MCP client config.

The OpenCode MCP CLIENT request timeout must exceed the longest server-side tool
execution. If it is shorter (it was 30000ms while the exec tool allows 90000ms),
the client gives up with `-32001 Request timed out` before the server finishes,
producing the retry storm seen in production.
"""

from __future__ import annotations

import json

from ralph.mcp.transport.opencode import build_opencode_provider_config
from ralph.timeout_defaults import EXEC_MAX_TIMEOUT_MS


def _ralph_server(existing: str | None = None) -> dict:
    config_text, _ = build_opencode_provider_config(existing, "http://127.0.0.1:9999/mcp")
    config = json.loads(config_text)
    server = config["mcp"]["ralph"]
    assert isinstance(server, dict)
    return server


def test_opencode_client_timeout_exceeds_max_exec_tool_timeout() -> None:
    timeout = _ralph_server()["timeout"]
    assert isinstance(timeout, int)
    # Must exceed the MAX (not just default) exec timeout, since an agent may raise
    # timeout_ms up to EXEC_MAX_TIMEOUT_MS; otherwise a max-length exec re-triggers
    # the -32001 client-timeout storm.
    assert timeout > EXEC_MAX_TIMEOUT_MS, (
        f"OpenCode MCP client timeout {timeout} must exceed the max exec timeout "
        f"{EXEC_MAX_TIMEOUT_MS}."
    )
