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


def _upstream_as_dict(s: UpstreamMcpServer, *, server_url_key: bool = False) -> dict[str, object]:
    """Convert an UpstreamMcpServer to a plain dict.

    Args:
        s: UpstreamMcpServer to convert.
        server_url_key: If True, use 'serverUrl' instead of 'url' (for AGY compatibility).
    """
    result: dict[str, object] = {
        "name": s.name,
        "transport": s.transport if s.transport else "http",
        "serverUrl" if server_url_key else "url": s.url,
    }
    if s.command:
        result["command"] = s.command
    if s.args:
        result["args"] = list(s.args)
    if s.env:
        result["env"] = dict(s.env)
    return result


def _merge_opencode(current_config: dict[str, object], unsafe_mode: bool) -> dict[str, object]:
    ralph_opencode_mcp = cast("dict[str, object]", current_config.get("mcp", dict[str, object]()))
    ralph_opencode = ralph_opencode_mcp.get(RALPH_MCP_SERVER_NAME)
    if not unsafe_mode:
        if ralph_opencode is not None:
            return {"mcp": {RALPH_MCP_SERVER_NAME: ralph_opencode}}
        return {"mcp": {}}
    existing_native = _load_opencode_mcp_servers_from_current_config(current_config)
    merged: dict[str, object] = dict(existing_native)
    if ralph_opencode is not None:
        merged[RALPH_MCP_SERVER_NAME] = ralph_opencode
    return {"mcp": merged}


def _merge_codex(current_config: dict[str, object], unsafe_mode: bool) -> dict[str, object]:
    existing_toml_servers = _load_codex_mcp_servers_from_current_config(current_config)
    codex_ralph_key = "mcp_servers." + RALPH_MCP_SERVER_NAME
    codex_ralph_entry = current_config.get(codex_ralph_key)
    if not unsafe_mode:
        if codex_ralph_entry is not None and isinstance(codex_ralph_entry, dict):
            return {codex_ralph_key: codex_ralph_entry}
        return {}
    result = dict(existing_toml_servers)
    if codex_ralph_entry is not None and isinstance(codex_ralph_entry, dict):
        result[codex_ralph_key] = codex_ralph_entry
    return result


def _merge_default(
    agent_name: str,
    current_config: dict[str, object],
    unsafe_mode: bool,
    workspace_path: object = None,
) -> dict[str, object]:
    ralph_mcp_servers = cast(
        "dict[str, object]", current_config.get("mcpServers", dict[str, object]())
    )
    ralph_entry = ralph_mcp_servers.get(RALPH_MCP_SERVER_NAME)
    if not unsafe_mode:
        if ralph_entry is not None:
            return {"mcpServers": {RALPH_MCP_SERVER_NAME: ralph_entry}}
        return {}
    upstreams = _load_upstreams_for_agent(agent_name, current_config, workspace_path)
    filtered_upstreams = tuple(s for s in upstreams if s.name != RALPH_MCP_SERVER_NAME)
    existing_map: dict[str, object] = {
        s.name: _upstream_as_dict(s, server_url_key=(agent_name == "agy"))
        for s in filtered_upstreams
    }
    merged = dict(existing_map)
    if ralph_entry is not None:
        merged[RALPH_MCP_SERVER_NAME] = ralph_entry
    return {"mcpServers": merged}


def merge_existing_upstreams(
    agent_name: str,
    current_config: dict[str, object],
    *,
    unsafe_mode: bool,
    workspace_path: object = None,
) -> dict[str, object]:
    """Merge existing upstream servers into current_config based on agent and unsafe_mode.

    This helper consolidates the unsafe_mode merge logic across the 4 JSON-based
    transport files (claude, agy, nanocoder, opencode) into one dispatcher.

    When unsafe_mode=False: returns only the ralph entry (existing upstreams dropped).
    When unsafe_mode=True: merges existing agent-native servers with the ralph entry.

    Codex uses TOML-style `mcp_servers.X` keys and is handled separately to preserve
    the native TOML structure and all per-entry fields.

    Args:
        agent_name: One of "claude", "agy", "nanocoder", "opencode", "codex".
        current_config: Agent-native config dict.
            - claude/agy/nanocoder: {"mcpServers": {"<name>": {...}}}
            - opencode: {"mcp": {"<name>": {"type", "url", "enabled", "timeout", ...}}}
            - codex: {"mcp_servers.X": {...}, ...}  (TOML-style keys)
        unsafe_mode: Whether to preserve existing upstream servers.
        workspace_path: Optional workspace path for workspace-level config files.

    Returns:
        Merged config dict with ralph entry and optionally existing upstreams.
    """
    if agent_name == "opencode":
        return _merge_opencode(current_config, unsafe_mode)
    if agent_name == "codex":
        return _merge_codex(current_config, unsafe_mode)
    return _merge_default(agent_name, current_config, unsafe_mode, workspace_path)


def _load_upstreams_for_agent(
    agent_name: str,
    current_config: dict[str, object],
    workspace_path: object = None,
) -> tuple[UpstreamMcpServer, ...]:
    """Load existing UpstreamMcpServer tuple for agent_name (unsafe_mode=True path).

    Uses importlib to defer imports and avoid circular dependency at module init time.
    All agent transport modules import _load_mcpservers_from_paths from this module,
    so top-level imports would create:
      common -> claude/agy/nanocoder -> common (not yet fully initialized)

    workspace_path is extracted from current_config if present, to allow callers
    to specify which workspace config files to load.

    For claude/agy/nanocoder: if current_config already contains a populated
    "mcpServers" dict with non-ralph servers (from a prior merge by e.g.
    build_nanocoder_mcp_config or agy_workspace_mcp_endpoint), use those
    directly instead of re-loading from disk. This preserves pre-merged env
    servers that would otherwise be lost when the caller passes a
    workspace_path but the file-based loader would only see file servers.
    """
    _workspace_path = (
        workspace_path if workspace_path is not None else current_config.get("workspace_path")
    )
    existing_mcp_servers = current_config.get("mcpServers")
    if agent_name in ("claude", "agy", "nanocoder", "cursor"):
        servers: dict[str, object] = {}
        if isinstance(existing_mcp_servers, dict) and len(existing_mcp_servers) > 1:
            servers = cast("dict[str, object]", existing_mcp_servers)
        elif agent_name == "claude":
            mod = importlib.import_module("ralph.mcp.transport.claude")
            paths = cast("tuple[Path, ...]", mod._claude_mcp_config_paths(_workspace_path))
            servers = cast("dict[str, object]", mod._load_mcpservers_from_paths(paths))
        elif agent_name == "agy":
            mod = importlib.import_module("ralph.mcp.transport.agy")
            paths = cast("tuple[Path, ...]", mod._agy_mcp_config_paths(_workspace_path))
            normalizer = cast(
                "Callable[[str, object], tuple[str, object] | None]",
                mod._normalize_agy_server_entry,
            )
            servers = cast(
                "dict[str, object]",
                mod._load_mcpservers_from_paths(paths, normalizer),
            )
        elif agent_name == "cursor":
            mod = importlib.import_module("ralph.mcp.transport.cursor")
            paths = cast("tuple[Path, ...]", mod._cursor_paths_to_consider(_workspace_path))
            normalizer = cast(
                "Callable[[str, object], tuple[str, object] | None]",
                mod._normalize_cursor_server_entry,
            )
            servers = cast(
                "dict[str, object]",
                mod._load_mcpservers_from_paths(paths, normalizer),
            )
        else:  # nanocoder
            mod = importlib.import_module("ralph.mcp.transport.nanocoder")
            paths = cast("tuple[Path, ...]", mod._nanocoder_mcp_config_paths(_workspace_path))
            servers = cast("dict[str, object]", mod._load_mcpservers_from_paths(paths))
        servers.pop(RALPH_MCP_SERVER_NAME, None)
        return normalize_upstream_mcp_servers(servers)
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
