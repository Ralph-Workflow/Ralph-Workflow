from __future__ import annotations

from ralph.mcp.protocol import env as mcp_env


def test_mcp_env_constants_are_stable() -> None:
    assert mcp_env.MCP_ENDPOINT_ENV == "RALPH_MCP_ENDPOINT"
    assert mcp_env.MCP_RUN_ID_ENV == "RALPH_MCP_RUN_ID"
    assert mcp_env.MCP_SESSION_ENV == "RALPH_MCP_SESSION_JSON"
    assert mcp_env.MCP_SESSION_FILE_ENV == "RALPH_MCP_SESSION_FILE"
    assert mcp_env.MCP_PREFLIGHT_TIMEOUT_MS_ENV == "RALPH_MCP_PREFLIGHT_TIMEOUT_MS"
    assert mcp_env.MCP_SUPERVISION_INTERVAL_MS_ENV == "RALPH_MCP_SUPERVISION_INTERVAL_MS"


def test_mcp_env_uses_string_enum_for_internal_typing() -> None:
    assert mcp_env.McpEnvVar.ENDPOINT == "RALPH_MCP_ENDPOINT"
    assert isinstance(mcp_env.McpEnvVar.SESSION_FILE, str)
