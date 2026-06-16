"""OpenCode-specific MCP transport helpers."""

from __future__ import annotations

import json
from typing import cast

from ralph.mcp.tools.names import (
    ALL_RALPH_TOOLS,
    OPENCODE_NATIVE_TOOLS_TO_DISABLE,
    OPENCODE_NATIVE_TOOLS_TO_KEEP,
    claude_tool_name,
)
from ralph.mcp.transport.common import merge_existing_upstreams
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers
from ralph.timeout_defaults import EXEC_MAX_TIMEOUT_MS

#: OpenCode MCP client request timeout (ms). MUST exceed the longest possible
#: server-side tool execution — otherwise the client gives up with `-32001 Request
#: timed out` before the server finishes, producing a retry storm. exec is capped
#: at EXEC_MAX_TIMEOUT_MS (the largest any tool can run); add headroom for server
#: startup + output drain so even a max-length exec finishes before the client.
#:
#: IMPORTANT: OpenCode IGNORES the documented per-server ``mcp.<server>.timeout``
#: field and hard-enforces the MCP SDK default (~60s). The setting it actually
#: honors is the global ``experimental.mcp_timeout`` (opencode issues #8701/#8121).
#: We set BOTH: the experimental key is the one that takes effect; the per-server
#: field is kept for forward-compat if/when opencode starts honoring it.
_OPENCODE_MCP_CLIENT_TIMEOUT_MS = EXEC_MAX_TIMEOUT_MS + 30_000


def merge_opencode_config_content(existing: str | None, endpoint: str) -> str:
    """Merge Ralph MCP endpoint into an existing OpenCode config and return JSON."""
    config_text, _upstreams = build_opencode_provider_config(existing, endpoint)
    return config_text


def build_opencode_provider_config(
    existing: str | None, endpoint: str, *, unsafe_mode: bool = False
) -> tuple[str, tuple[UpstreamMcpServer, ...]]:
    """Build a full OpenCode config JSON with Ralph MCP and return it with upstream servers."""
    config_obj = _parse_opencode_config_content(existing)
    existing_mcp = config_obj.get("mcp")
    if isinstance(existing_mcp, dict):
        if unsafe_mode:
            existing_for_upstreams = {
                name: entry
                for name, entry in cast("dict[str, object]", existing_mcp).items()
                if name != "ralph"
            }
        else:
            existing_for_upstreams = cast("dict[str, object]", existing_mcp)
        upstreams = normalize_upstream_mcp_servers(existing_for_upstreams)
    else:
        upstreams = ()

    ralph_entry: dict[str, object] = {
        "type": "remote",
        "url": endpoint,
        "enabled": True,
        "timeout": _OPENCODE_MCP_CLIENT_TIMEOUT_MS,
    }
    current_config_mcp: dict[str, object] = (
        dict(cast("dict[str, object]", existing_mcp)) if isinstance(existing_mcp, dict) else {}
    )
    current_config_mcp["ralph"] = ralph_entry
    current_config: dict[str, object] = {"mcp": current_config_mcp}
    merged = merge_existing_upstreams(
        "opencode", current_config, unsafe_mode=unsafe_mode
    )
    config_obj["mcp"] = merged.get("mcp", {"ralph": ralph_entry})

    # The field OpenCode actually honors for the MCP request timeout (the per-server
    # `timeout` above is ignored). Without this, long tool calls (exec running tests/
    # builds, large reads) die at OpenCode's ~60s default with `-32001`.
    experimental_obj = config_obj.setdefault("experimental", {})
    if not isinstance(experimental_obj, dict):
        experimental_obj = {}
        config_obj["experimental"] = experimental_obj
    cast("dict[str, object]", experimental_obj)["mcp_timeout"] = _OPENCODE_MCP_CLIENT_TIMEOUT_MS

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
    # Native orchestration tools (sub-agents, skills, todos, web) stay enabled and
    # must be auto-allowed so they cannot wedge a headless run on an approval prompt.
    for native_name in OPENCODE_NATIVE_TOOLS_TO_KEEP:
        permission_section[native_name] = "allow"

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
