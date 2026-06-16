"""Shared MCP transport helpers: mcp.toml loading, upstream merging, env serialization."""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.config.mcp_loader import load_mcp_config
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UpstreamMcpServer,
    normalize_upstream_mcp_servers,
    serialize_upstream_mcp_servers,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.mcp_models import McpConfig


def mcp_toml_as_upstreams(workspace_path: Path | None) -> tuple[UpstreamMcpServer, ...]:
    """Load .agent/mcp.toml and return the configured upstream MCP servers."""
    config_path = (workspace_path / ".agent" / "mcp.toml") if workspace_path is not None else None
    mcp_config = load_mcp_config(config_path=config_path)
    return mcp_config_as_upstreams(mcp_config)


def _parse_json_config_file(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        raw_payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw_payload, dict):
        return {}
    return cast("dict[str, object]", raw_payload)


def _load_mcpservers_from_paths(
    paths: tuple[Path, ...],
    entry_normalizer: Callable[[str, object], tuple[str, object] | None] | None = None,
) -> dict[str, object]:
    merged: dict[str, object] = {}
    for path in paths:
        config_obj = _parse_json_config_file(path)
        if not config_obj:
            continue
        value = config_obj.get("mcpServers")
        if not isinstance(value, dict):
            continue
        server_entries = cast("dict[str, object]", value)
        if entry_normalizer is None:
            merged.update(server_entries)
            continue
        for server_name, server_entry in server_entries.items():
            normalized = entry_normalizer(server_name, server_entry)
            if normalized is not None:
                merged[normalized[0]] = normalized[1]
    return merged


def mcp_config_as_upstreams(mcp_config: McpConfig) -> tuple[UpstreamMcpServer, ...]:
    """Convert loaded MCP config into Ralph custom upstream server records."""
    return tuple(
        UpstreamMcpServer(
            name=spec.name,
            transport=spec.transport,
            url=spec.url,
            command=spec.command,
            args=tuple(spec.args),
            env=dict(spec.env),
            origin="custom",
        )
        for spec in mcp_config.mcp_servers.values()
    )


def merge_mcp_toml_into_upstreams(
    agent_native: tuple[UpstreamMcpServer, ...],
    mcp_toml_servers: tuple[UpstreamMcpServer, ...],
) -> tuple[UpstreamMcpServer, ...]:
    """Merge mcp.toml servers into agent-native upstreams, preferring mcp.toml on conflict."""
    merged: dict[str, UpstreamMcpServer] = {s.name: s for s in agent_native}
    for server in mcp_toml_servers:
        if server.name in merged:
            logger.warning(
                "mcp.toml server '{}' overrides agent-native upstream config",
                server.name,
            )
        merged[server.name] = server
    return tuple(merged.values())


def set_upstream_mcp_config(
    runtime_env: dict[str, str], upstreams: tuple[UpstreamMcpServer, ...]
) -> None:
    """Inject upstream MCP config into the runtime environment dict."""
    if upstreams:
        runtime_env[UPSTREAM_MCP_CONFIG_ENV] = serialize_upstream_mcp_servers(upstreams)
        return
    runtime_env.pop(UPSTREAM_MCP_CONFIG_ENV, None)


def _load_opencode_mcp_servers_from_current_config(
    current_config: dict[str, object],
) -> dict[str, object]:
    """Extract opencode mcp servers from a current_config dict."""
    mcp_entry = current_config.get("mcp")
    if not isinstance(mcp_entry, dict):
        return {}
    return cast("dict[str, object]", mcp_entry)


def _load_codex_mcp_servers_from_current_config(
    current_config: dict[str, object],
) -> dict[str, object]:
    """Extract codex mcp servers (TOML-style [mcp_servers.*]) from a current_config dict."""
    servers: dict[str, object] = {}
    for key, value in current_config.items():
        if not isinstance(key, str) or not key.startswith("mcp_servers."):
            continue
        if isinstance(value, dict):
            servers[key] = value
    return servers


def _upstream_as_dict(s: UpstreamMcpServer) -> dict[str, object]:
    """Convert an UpstreamMcpServer to a plain dict."""
    result: dict[str, object] = {
        "name": s.name,
        "transport": s.transport if s.transport else "http",
        "url": s.url,
    }
    if s.command:
        result["command"] = s.command
    if s.args:
        result["args"] = list(s.args)
    if s.env:
        result["env"] = dict(s.env)
    return result


def merge_existing_upstreams(
    agent_name: str,
    current_config: dict[str, object],
    *,
    unsafe_mode: bool,
) -> dict[str, object]:
    """Merge existing upstream servers into current_config based on agent and unsafe_mode.

    This helper consolidates the unsafe_mode merge logic across all 5 transport files
    (claude, agy, nanocoder, opencode, codex) into one dispatcher.

    When unsafe_mode=False: returns current_config with non-ralph entries dropped.
    When unsafe_mode=True: merges existing agent-native servers with current_config.

    Args:
        agent_name: One of "claude", "agy", "nanocoder", "opencode", "codex".
        current_config: Agent-native config dict (e.g. {"mcpServers": {...}} for claude/agy).
        unsafe_mode: Whether to preserve existing upstream servers.

    Returns:
        Merged config dict with ralph entry and optionally existing upstreams.
    """
    ralph_mcp_servers = cast(
        "dict[str, object]", current_config.get("mcpServers", dict[str, object]())
    )
    ralph_entry = ralph_mcp_servers.get(RALPH_MCP_SERVER_NAME)
    ralph_opencode_mcp = cast(
        "dict[str, object]", current_config.get("mcp", dict[str, object]())
    )
    ralph_opencode = ralph_opencode_mcp.get(RALPH_MCP_SERVER_NAME)

    if not unsafe_mode:
        result: dict[str, object] | None = None
        if ralph_entry is not None:
            result = {"mcpServers": {RALPH_MCP_SERVER_NAME: ralph_entry}}
        elif ralph_opencode is not None:
            result = {"mcp": {RALPH_MCP_SERVER_NAME: ralph_opencode}}
        return result if result is not None else current_config

    upstreams = _load_upstreams_for_agent(agent_name, current_config)
    existing_map: dict[str, object] = {s.name: _upstream_as_dict(s) for s in upstreams}

    if agent_name in ("claude", "agy", "nanocoder"):
        res: dict[str, object] = dict(existing_map)
        if ralph_entry is not None:
            res[RALPH_MCP_SERVER_NAME] = ralph_entry
        return {"mcpServers": res}
    if agent_name == "opencode":
        res = dict(existing_map)
        if ralph_opencode is not None:
            res[RALPH_MCP_SERVER_NAME] = ralph_opencode
        return {"mcp": res}
    if agent_name == "codex":
        res = dict(existing_map)
        if ralph_entry is not None:
            res[RALPH_MCP_SERVER_NAME] = ralph_entry
        return {"mcpServers": res}
    return current_config


def _load_upstreams_for_agent(
    agent_name: str, current_config: dict[str, object]
) -> tuple[UpstreamMcpServer, ...]:
    """Load existing UpstreamMcpServer tuple for agent_name (unsafe_mode=True path).

    Uses importlib to defer imports and avoid circular dependency at module init time.
    All agent transport modules import _load_mcpservers_from_paths from this module,
    so top-level imports would create:
      common -> claude/agy/nanocoder -> common (not yet fully initialized)
    """
    if agent_name == "claude":
        mod = importlib.import_module("ralph.mcp.transport.claude")
        return cast("tuple[UpstreamMcpServer, ...]", mod.load_existing_claude_upstream_servers())
    if agent_name == "agy":
        mod = importlib.import_module("ralph.mcp.transport.agy")
        return cast("tuple[UpstreamMcpServer, ...]", mod.load_existing_agy_upstream_servers())
    if agent_name == "nanocoder":
        mod = importlib.import_module("ralph.mcp.transport.nanocoder")
        return cast(
            "tuple[UpstreamMcpServer, ...]",
            mod.load_existing_nanocoder_upstream_servers(None),
        )
    if agent_name == "opencode":
        existing = _load_opencode_mcp_servers_from_current_config(current_config)
        return normalize_upstream_mcp_servers(existing)
    if agent_name == "codex":
        existing = _load_codex_mcp_servers_from_current_config(current_config)
        return normalize_upstream_mcp_servers(existing)
    return ()


__all__ = [
    "mcp_config_as_upstreams",
    "mcp_toml_as_upstreams",
    "merge_existing_upstreams",
    "merge_mcp_toml_into_upstreams",
    "set_upstream_mcp_config",
]
