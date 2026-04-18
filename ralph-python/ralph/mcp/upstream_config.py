"""Transport-neutral upstream MCP config normalization helpers."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal, cast

from ralph.mcp.tool_names import RALPH_MCP_SERVER_NAME

UPSTREAM_MCP_CONFIG_ENV = "RALPH_UPSTREAM_MCP_CONFIG"


class UpstreamConfigError(ValueError):
    """Raised when upstream MCP config violates Ralph's strict-mode contract."""


@dataclass(frozen=True)
class UpstreamMcpServer:
    """Normalized upstream MCP server definition for Ralph runtime use."""

    name: str
    transport: Literal["http", "stdio"]
    url: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


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
        }
        for server in servers
    ]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def load_upstream_mcp_servers(raw: str | None) -> tuple[UpstreamMcpServer, ...]:
    """Decode upstream MCP servers from their serialized environment payload."""

    if not raw:
        return ()
    decoded: object = json.loads(raw)
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
            )
        )
    return tuple(servers)


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
