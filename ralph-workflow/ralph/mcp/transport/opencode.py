"""OpenCode-specific MCP transport helpers."""

from __future__ import annotations

import json
from typing import cast

from ralph.mcp.tools.names import (
    ALL_RALPH_TOOLS,
    OPENCODE_NATIVE_TOOLS_TO_DISABLE,
    claude_tool_name,
)
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers
from ralph.timeout_defaults import EXEC_MAX_TIMEOUT_MS

#: OpenCode MCP client request timeout (ms). MUST exceed the longest possible
#: server-side tool execution — otherwise the client gives up with `-32001 Request
#: timed out` before the server finishes, producing a retry storm. exec is capped
#: at EXEC_MAX_TIMEOUT_MS (the largest any tool can run); add headroom for server
#: startup + output drain so even a max-length exec finishes before the client.
_OPENCODE_MCP_CLIENT_TIMEOUT_MS = EXEC_MAX_TIMEOUT_MS + 30_000


def merge_opencode_config_content(existing: str | None, endpoint: str) -> str:
    """Merge Ralph MCP endpoint into an existing OpenCode config and return JSON."""
    config_text, _upstreams = build_opencode_provider_config(existing, endpoint)
    return config_text


def build_opencode_provider_config(
    existing: str | None, endpoint: str
) -> tuple[str, tuple[UpstreamMcpServer, ...]]:
    """Build a full OpenCode config JSON with Ralph MCP and return it with upstream servers."""
    config_obj = _parse_opencode_config_content(existing)
    existing_mcp = config_obj.get("mcp")
    upstreams = (
        normalize_upstream_mcp_servers(cast("dict[str, object]", existing_mcp))
        if isinstance(existing_mcp, dict)
        else ()
    )

    config_obj["mcp"] = {
        "ralph": {
            "type": "remote",
            "url": endpoint,
            "enabled": True,
            "timeout": _OPENCODE_MCP_CLIENT_TIMEOUT_MS,
        }
    }

    permission_section_obj = config_obj.setdefault("permission", {})
    if not isinstance(permission_section_obj, dict):
        permission_section_obj = {}
        config_obj["permission"] = permission_section_obj
    permission_section = cast("dict[str, object]", permission_section_obj)
    permission_section["ralph_*"] = "allow"
    permission_section["mcp__ralph__*"] = "allow"
    for tool_name in ALL_RALPH_TOOLS:
        bare_name = str(tool_name)
        permission_section[bare_name] = "allow"
        permission_section[claude_tool_name(bare_name)] = "allow"

    existing_tools = config_obj.get("tools", {})
    if not isinstance(existing_tools, dict):
        existing_tools = {}
    disable_overrides = dict.fromkeys(OPENCODE_NATIVE_TOOLS_TO_DISABLE, False)
    config_obj["tools"] = {**cast("dict[str, object]", existing_tools), **disable_overrides}

    config_obj.setdefault("$schema", "https://opencode.ai/config.json")
    return json.dumps(config_obj, sort_keys=True), upstreams


def _parse_opencode_config_content(existing: str | None) -> dict[str, object]:
    if not existing:
        return {}
    try:
        decoded: object = json.loads(existing)
    except json.JSONDecodeError:
        return {}
    if not isinstance(decoded, dict):
        return {}
    return cast("dict[str, object]", decoded)


__all__ = [
    "build_opencode_provider_config",
    "merge_opencode_config_content",
]
