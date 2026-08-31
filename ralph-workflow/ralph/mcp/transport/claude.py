"""Claude-specific MCP transport helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

from loguru import logger

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.transport.common import merge_existing_upstreams
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers

_USER_CONFIG_FILENAME = ".claude.json"
_MCP_JSON_FILENAME = ".mcp.json"
_CLAUDE_HOME_DIRNAME = ".claude"
_SETTINGS_FILENAMES = ("settings.json", "settings.local.json")
_PLUGINS_DIRNAME = "plugins"
_INSTALLED_PLUGINS_FILENAME = "installed_plugins.json"
_PLUGIN_ALIAS_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def claude_mcp_config(
    endpoint: str,
    *,
    workspace_path: Path | None = None,
    unsafe_mode: bool = False,
) -> str:
    """Return the Claude MCP JSON config string pointing to the given endpoint."""
    ralph_entry = {
        RALPH_MCP_SERVER_NAME: {
            "type": "http",
            "url": endpoint,
        }
    }
    current_config: dict[str, object] = {"mcpServers": dict(ralph_entry)}
    if workspace_path is not None:
        current_config["workspace_path"] = workspace_path
    merged_config = merge_existing_upstreams(
        "claude", current_config, unsafe_mode=unsafe_mode, workspace_path=workspace_path
    )
    config_payload = merged_config
    return json.dumps(config_payload, separators=(",", ":"))


def load_existing_claude_upstream_servers(
    workspace_path: Path | None = None,
) -> tuple[UpstreamMcpServer, ...]:
    """Read every place Claude takes MCP servers from and return them as upstreams.

    Ralph runs Claude with ``--strict-mcp-config``, which makes the generated
    ``--mcp-config`` the *only* MCP source Claude sees. Every server this
    function fails to find is therefore deleted from the operator's session
    rather than re-exposed as a ``ralph_upstream__*`` proxy, so the source list
    below has to match Claude's own, lowest precedence first:

    1. enabled plugins' ``<plugin-root>/.mcp.json``
    2. ``~/.claude.json`` -> ``mcpServers`` (user scope)
    3. ``<workspace>/.mcp.json`` (project scope)
    4. ``<workspace>/.claude.json``
    5. ``~/.claude.json`` -> ``projects.<workspace>.mcpServers`` (local scope,
       where a plain ``claude mcp add`` writes)

    Ralph's own entry is dropped first. ``ralph`` is a reserved upstream name
    and ``normalize_upstream_mcp_servers`` raises on it, so an operator config
    that already names it -- one written by hand, or copied back from a config
    Ralph synthesized -- used to abort every Claude invocation instead of being
    replaced by the live endpoint.
    """
    servers: dict[str, object] = dict(_enabled_plugin_mcp_servers(workspace_path))
    servers.update(_load_mcpservers_from_paths(_claude_mcp_config_paths(workspace_path)))
    servers.update(_local_scope_mcp_servers(workspace_path))
    servers.pop(RALPH_MCP_SERVER_NAME, None)
    return normalize_upstream_mcp_servers(servers)


def _claude_mcp_config_paths(workspace_path: Path | None) -> tuple[Path, ...]:
    """Return the plain ``{"mcpServers": ...}`` files, lowest precedence first."""
    workspace_paths: tuple[Path, ...] = ()
    if workspace_path is not None:
        workspace_paths = (
            workspace_path / _MCP_JSON_FILENAME,
            workspace_path / _USER_CONFIG_FILENAME,
        )
    return (
        Path.home() / _USER_CONFIG_FILENAME,
        *workspace_paths,
    )


def _load_mcpservers_from_paths(paths: tuple[Path, ...]) -> dict[str, object]:
    """Merge the ``mcpServers`` map of each path, later paths winning.

    Claude keeps its own copy of this loader rather than using the shared one in
    ``transport.common``: that one returns an empty map for a file it cannot
    parse, which is indistinguishable from "the operator has no servers" at the
    exact moment Ralph is about to strip them all. ``transport.common``'s
    ``_load_upstreams_for_agent`` resolves this name on this module, so its
    unsafe-mode merge picks the same behaviour up.
    """
    merged: dict[str, object] = {}
    for path in paths:
        merged.update(_mcp_servers_from_file(path))
    return merged


def _mcp_servers_from_file(path: Path) -> dict[str, object]:
    config = _read_json_object(path)
    if config is None:
        return {}
    return _mcp_servers_map(config, path)


def _mcp_servers_map(config: dict[str, object], path: Path) -> dict[str, object]:
    value = config.get("mcpServers")
    if value is None:
        return {}
    if not isinstance(value, dict):
        logger.warning(
            "Claude MCP config {} has a non-object 'mcpServers'; any server it "
            "meant to define will be missing from this run.",
            path,
        )
        return {}
    return dict(cast("dict[str, object]", value))


def _read_json_object(path: Path) -> dict[str, object] | None:
    """Parse ``path`` as a JSON object, warning rather than failing silently."""
    if not path.exists():
        return None
    try:
        raw_payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        logger.warning(
            "Cannot read Claude config {}: {}. Ralph runs Claude with "
            "--strict-mcp-config, so any MCP server this file defines is absent "
            "from this run.",
            path,
            error,
        )
        return None
    if not isinstance(raw_payload, dict):
        logger.warning(
            "Claude config {} is not a JSON object; ignoring it and any MCP server it defines.",
            path,
        )
        return None
    return cast("dict[str, object]", raw_payload)


def _local_scope_mcp_servers(workspace_path: Path | None) -> dict[str, object]:
    """Return ``~/.claude.json`` -> ``projects.<workspace>.mcpServers``.

    This is where a plain ``claude mcp add`` puts a server: ``--scope`` defaults
    to ``local``, which is per-project and stored in the user config rather than
    in the workspace.
    """
    if workspace_path is None:
        return {}
    user_config_path = Path.home() / _USER_CONFIG_FILENAME
    config = _read_json_object(user_config_path)
    if config is None:
        return {}
    projects = config.get("projects")
    if not isinstance(projects, dict):
        return {}
    project_entries = cast("dict[str, object]", projects)
    for key in _workspace_config_keys(workspace_path):
        entry = project_entries.get(key)
        if isinstance(entry, dict):
            return _mcp_servers_map(cast("dict[str, object]", entry), user_config_path)
    return {}


def _workspace_config_keys(workspace_path: Path) -> tuple[str, ...]:
    """Return the keys Claude may have stored this workspace under."""
    literal = str(workspace_path)
    resolved = str(workspace_path.resolve())
    if resolved == literal:
        return (literal,)
    return (literal, resolved)


def _enabled_plugin_mcp_servers(workspace_path: Path | None) -> dict[str, object]:
    """Return the MCP servers shipped by the plugins the operator has enabled.

    A plugin declares them in ``<plugin-root>/.mcp.json`` and Claude exposes
    them as ``plugin:<plugin>:<server>``. Dropping them is the defect that made
    Ralph run OpenCode with ``--pure``: a plugin is often what supplies the
    capability the operator depends on.
    """
    enabled_keys = _enabled_plugin_keys(workspace_path)
    if not enabled_keys:
        return {}
    install_paths = _installed_plugin_paths()
    servers: dict[str, object] = {}
    for plugin_key in sorted(enabled_keys):
        plugin_name = plugin_key.split("@", 1)[0]
        for install_path in install_paths.get(plugin_key, ()):
            entries = _mcp_servers_from_file(install_path / _MCP_JSON_FILENAME)
            for server_name, server_entry in entries.items():
                servers[_plugin_server_alias(plugin_name, server_name)] = server_entry
    return servers


def _plugin_server_alias(plugin_name: str, server_name: str) -> str:
    """Namespace a plugin server the way Claude does, with a tool-name-safe separator.

    Claude calls it ``plugin:<plugin>:<server>``; ``:`` cannot appear in the
    ``ralph_upstream__<server>__<tool>`` alias Ralph re-exposes it under, so the
    same namespacing is spelled with ``_``. Without it a plugin's ``playwright``
    would silently replace the operator's own ``playwright``.
    """
    safe_plugin = _PLUGIN_ALIAS_UNSAFE.sub("_", plugin_name)
    safe_server = _PLUGIN_ALIAS_UNSAFE.sub("_", server_name)
    return f"plugin_{safe_plugin}_{safe_server}"


def _enabled_plugin_keys(workspace_path: Path | None) -> frozenset[str]:
    """Return the ``<plugin>@<marketplace>`` keys enabled across the settings files."""
    enabled: dict[str, bool] = {}
    for path in _claude_settings_paths(workspace_path):
        config = _read_json_object(path)
        if config is None:
            continue
        value = config.get("enabledPlugins")
        if not isinstance(value, dict):
            continue
        enabled.update(
            {
                plugin_key: flag
                for plugin_key, flag in cast("dict[str, object]", value).items()
                if isinstance(flag, bool)
            }
        )
    return frozenset(plugin_key for plugin_key, flag in enabled.items() if flag)


def _claude_settings_paths(workspace_path: Path | None) -> tuple[Path, ...]:
    """Return the settings files that record which plugins are enabled."""
    home_settings_dir = Path.home() / _CLAUDE_HOME_DIRNAME
    paths = [home_settings_dir / name for name in _SETTINGS_FILENAMES]
    if workspace_path is not None:
        workspace_settings_dir = workspace_path / _CLAUDE_HOME_DIRNAME
        paths.extend(workspace_settings_dir / name for name in _SETTINGS_FILENAMES)
    return tuple(paths)


def _installed_plugin_paths() -> dict[str, tuple[Path, ...]]:
    """Map each installed ``<plugin>@<marketplace>`` key to its install directories."""
    installed_path = (
        Path.home() / _CLAUDE_HOME_DIRNAME / _PLUGINS_DIRNAME / _INSTALLED_PLUGINS_FILENAME
    )
    config = _read_json_object(installed_path)
    if config is None:
        return {}
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        return {}
    resolved: dict[str, tuple[Path, ...]] = {}
    for plugin_key, raw_entries in cast("dict[str, object]", plugins).items():
        if not isinstance(raw_entries, list):
            continue
        install_paths = tuple(_install_path(entry) for entry in cast("list[object]", raw_entries))
        present = tuple(path for path in install_paths if path is not None)
        if present:
            resolved[plugin_key] = present
    return resolved


def _install_path(raw_entry: object) -> Path | None:
    if not isinstance(raw_entry, dict):
        return None
    install_path = cast("dict[str, object]", raw_entry).get("installPath")
    if isinstance(install_path, str) and install_path:
        return Path(install_path)
    return None


__all__ = [
    "claude_mcp_config",
    "load_existing_claude_upstream_servers",
]
