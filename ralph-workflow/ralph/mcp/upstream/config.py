"""Transport-neutral upstream MCP config normalization helpers."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal, cast

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.upstream.upstream_config_error import UpstreamConfigError
from ralph.mcp.upstream.upstream_tool import UpstreamTool

logger = logging.getLogger(__name__)

UPSTREAM_MCP_CONFIG_ENV = "RALPH_UPSTREAM_MCP_CONFIG"
UPSTREAM_MCP_TOOL_CATALOG_ENV = "RALPH_UPSTREAM_MCP_TOOL_CATALOG"
McpServerOrigin = Literal["custom", "agent_upstream"]


@dataclass(frozen=True)
class UpstreamMcpServer:
    """Normalized upstream MCP server definition for Ralph runtime use."""

    name: str
    transport: Literal["http", "stdio"]
    url: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    origin: McpServerOrigin = "agent_upstream"


def normalize_upstream_mcp_servers(
    server_entries: Mapping[str, object],
) -> tuple[UpstreamMcpServer, ...]:
    """Normalize provider-specific MCP server maps into Ralph runtime definitions."""

    normalized: list[UpstreamMcpServer] = []
    for name, raw_entry in server_entries.items():
        if name == RALPH_MCP_SERVER_NAME:
            msg = (
                f"upstream MCP server name '{RALPH_MCP_SERVER_NAME}'"
                " is reserved for Ralph strict mode"
            )
            raise UpstreamConfigError(msg)
        if not isinstance(raw_entry, Mapping):
            continue

        entry = cast("Mapping[str, object]", raw_entry)
        url = entry.get("url")
        command = entry.get("command")

        if isinstance(url, str) and url:
            normalized.append(
                UpstreamMcpServer(
                    name=name,
                    transport="http",
                    url=url,
                    env=_env_mapping(entry.get("env")),
                    origin="agent_upstream",
                )
            )
            continue

        if isinstance(command, str) and command:
            normalized.append(
                UpstreamMcpServer(
                    name=name,
                    transport="stdio",
                    command=command,
                    args=_args_tuple(entry.get("args")),
                    env=_env_mapping(entry.get("env")),
                    origin="agent_upstream",
                )
            )

    return tuple(normalized)


def serialize_upstream_mcp_servers(servers: Iterable[UpstreamMcpServer]) -> str:
    """Serialize normalized upstream servers for process environment transport."""

    payload = [
        {
            "name": server.name,
            "transport": server.transport,
            "url": server.url,
            "command": server.command,
            "args": list(server.args),
            "env": dict(server.env),
            "origin": server.origin,
        }
        for server in servers
    ]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def load_upstream_mcp_servers(raw: str | None) -> tuple[UpstreamMcpServer, ...]:
    """Decode upstream MCP servers from their serialized environment payload."""

    if not raw:
        return ()
    try:
        decoded: object = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "RALPH_UPSTREAM_MCP_CONFIG contains invalid JSON; ignoring upstream servers."
        )
        return ()
    if not isinstance(decoded, list):
        return ()

    servers: list[UpstreamMcpServer] = []
    for item in decoded:
        if not isinstance(item, Mapping):
            continue
        item_map = cast("Mapping[str, object]", item)
        name = item_map.get("name")
        transport = item_map.get("transport")
        if not isinstance(name, str) or transport not in {"http", "stdio"}:
            continue
        servers.append(
            UpstreamMcpServer(
                name=name,
                transport=cast('Literal["http", "stdio"]', transport),
                url=_optional_str(item_map.get("url")),
                command=_optional_str(item_map.get("command")),
                args=_args_tuple(item_map.get("args")),
                env=_env_mapping(item_map.get("env")),
                origin=_origin_value(item_map.get("origin")),
            )
        )
    return tuple(servers)


def serialize_upstream_tool_catalog(
    tool_catalog: Mapping[str, Iterable[UpstreamTool]],
) -> str:
    """Serialize discovered upstream tool metadata for process environment transport."""

    payload = {
        server_name: [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": dict(tool.input_schema),
            }
            for tool in tools
        ]
        for server_name, tools in tool_catalog.items()
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def load_upstream_tool_catalog(raw: str | None) -> dict[str, list[UpstreamTool]]:
    """Decode upstream tool metadata from its serialized environment payload."""

    if not raw:
        return {}
    try:
        decoded: object = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "RALPH_UPSTREAM_MCP_TOOL_CATALOG contains invalid JSON; ignoring tool catalog."
        )
        return {}
    if not isinstance(decoded, Mapping):
        return {}

    catalog: dict[str, list[UpstreamTool]] = {}
    for server_name, raw_tools in decoded.items():
        if not isinstance(server_name, str) or not isinstance(raw_tools, list):
            continue
        tools: list[UpstreamTool] = []
        for raw_tool in raw_tools:
            if not isinstance(raw_tool, Mapping):
                continue
            name = raw_tool.get("name")
            if not isinstance(name, str) or not name:
                continue
            description = raw_tool.get("description")
            input_schema_raw = raw_tool.get("input_schema")
            input_schema = (
                dict(cast("Mapping[str, object]", input_schema_raw))
                if isinstance(input_schema_raw, Mapping)
                else {}
            )
            tools.append(
                UpstreamTool(
                    name=name,
                    description=str(description) if description is not None else "",
                    input_schema=input_schema,
                )
            )
        if tools:
            catalog[server_name] = tools
    return catalog


def _args_tuple(raw_args: object) -> tuple[str, ...]:
    if not isinstance(raw_args, list):
        return ()
    return tuple(str(arg) for arg in raw_args if isinstance(arg, str))


def _env_mapping(raw_env: object) -> dict[str, str]:
    if not isinstance(raw_env, Mapping):
        return {}
    return {str(key): value for key, value in raw_env.items() if isinstance(value, str)}


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _origin_value(value: object) -> McpServerOrigin:
    if isinstance(value, str) and value in {"custom", "agent_upstream"}:
        return cast("McpServerOrigin", value)
    return "agent_upstream"


__all__ = [
    "UPSTREAM_MCP_CONFIG_ENV",
    "UPSTREAM_MCP_TOOL_CATALOG_ENV",
    "McpServerOrigin",
    "UpstreamMcpServer",
    "load_upstream_mcp_servers",
    "load_upstream_tool_catalog",
    "normalize_upstream_mcp_servers",
    "serialize_upstream_mcp_servers",
    "serialize_upstream_tool_catalog",
]
