
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from loguru import logger

from ralph.executor.process import ProcessExecutionError, ProcessRunOptions, run_process
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.transport.common import merge_existing_upstreams
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers
from ralph.timeout_defaults import EXEC_SOFT_REPLY_BUDGET_MS

if TYPE_CHECKING:
    from collections.abc import Callable

_USER_CONFIG_FILENAME = ".claude.json"
_MCP_JSON_FILENAME = ".mcp.json"
_CLAUDE_HOME_DIRNAME = ".claude"
_SETTINGS_FILENAMES = ("settings.json", "settings.local.json")
_PLUGINS_DIRNAME = "plugins"
_INSTALLED_PLUGINS_FILENAME = "installed_plugins.json"
_PLUGIN_ALIAS_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
_CLAUDE_EXECUTABLE = "claude"
_MCP_LIST_TIMEOUT_SECONDS: Final = 20.0
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_MCP_LIST_ENTRY = re.compile(r"^(?P<name>[^:]+): \S")
_ALIAS_UNSAFE_IN_CLI_NAME = ":"


_CLAUDE_TOOL_TIMEOUT_HEADROOM_MS: Final[int] = 30_000

_CLAUDE_TOOL_TIMEOUT_MS: Final[int] = EXEC_SOFT_REPLY_BUDGET_MS + _CLAUDE_TOOL_TIMEOUT_HEADROOM_MS
CLAUDE_TOOL_TIMEOUT_MS: Final[int] = _CLAUDE_TOOL_TIMEOUT_MS


def claude_mcp_config(
    endpoint: str,
    *,
    workspace_path: Path | None = None,
    unsafe_mode: bool = False,
) -> str:
    ralph_entry = {
        RALPH_MCP_SERVER_NAME: {
            "type": "http",
            "url": endpoint,
            "timeout": _CLAUDE_TOOL_TIMEOUT_MS,
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
    servers: dict[str, object] = dict(_enabled_plugin_mcp_servers(workspace_path))
    servers.update(_mcp_servers_from_file(Path.home() / _USER_CONFIG_FILENAME))
    servers.update(_project_scope_mcp_servers(workspace_path))
    if workspace_path is not None:
        servers.update(_mcp_servers_from_file(workspace_path / _USER_CONFIG_FILENAME))
    servers.update(_local_scope_mcp_servers(workspace_path))
    servers.pop(RALPH_MCP_SERVER_NAME, None)
    return normalize_upstream_mcp_servers(servers)


def list_claude_cli_mcp_server_names() -> tuple[str, ...] | None:
    try:
        completed = run_process(
            _CLAUDE_EXECUTABLE,
            ("mcp", "list"),
            options=ProcessRunOptions(
                capture_output=True,
                timeout=_MCP_LIST_TIMEOUT_SECONDS,
                label="mcp:claude-mcp-list",
            ),
        )
    except (OSError, ProcessExecutionError):
        return None
    if completed.returncode != 0:
        return None
    return parse_claude_mcp_list_names(completed.stdout)


claude_cli_mcp_server_lister: Callable[[], tuple[str, ...] | None] = (
    list_claude_cli_mcp_server_names
)


def parse_claude_mcp_list_names(output: str) -> tuple[str, ...]:
    names: list[str] = []
    for raw_line in output.splitlines():
        line = _ANSI_ESCAPE.sub("", raw_line).strip()
        match = _MCP_LIST_ENTRY.match(line)
        if match is None:
            continue
        matched = match.group("name")
        if not isinstance(matched, str):
            continue
        name = matched.strip()
        if name and name not in names:
            names.append(name)
    return tuple(names)


def claude_mcp_servers_ralph_cannot_proxy(
    cli_server_names: tuple[str, ...] | None,
    discovered: tuple[UpstreamMcpServer, ...],
) -> tuple[str, ...]:
    if cli_server_names is None:
        return ()
    proxied = {server.name for server in discovered}
    return tuple(
        name
        for name in cli_server_names
        if name not in proxied and _cli_name_as_ralph_alias(name) not in proxied
    )


def _cli_name_as_ralph_alias(cli_server_name: str) -> str:
    return cli_server_name.replace(_ALIAS_UNSAFE_IN_CLI_NAME, "_")


_PROXY_REPORT_MEMO: list[tuple[str, tuple[str, ...]] | None] = [None]


def reset_claude_mcp_proxy_report() -> None:
    _PROXY_REPORT_MEMO[0] = None


def report_claude_mcp_servers_ralph_cannot_proxy(
    workspace_path: Path | None,
) -> tuple[str, ...]:
    memo_key = "" if workspace_path is None else str(workspace_path)
    memoized = _PROXY_REPORT_MEMO[0]
    if memoized is not None and memoized[0] == memo_key:
        return memoized[1]
    missing = claude_mcp_servers_ralph_cannot_proxy(
        claude_cli_mcp_server_lister(),
        load_existing_claude_upstream_servers(workspace_path),
    )
    _PROXY_REPORT_MEMO[0] = (memo_key, missing)
    if missing:
        logger.warning(
            "Claude has {} MCP server(s) Ralph Workflow cannot re-expose and this "
            "run will not have: {}. Ralph Workflow passes --strict-mcp-config, which "
            "makes its own --mcp-config the only MCP source Claude reads, and these "
            "servers have no on-disk definition for Ralph Workflow to proxy back "
            "(claude.ai account connectors and session-only --plugin-dir/--plugin-url "
            "plugins are delivered by the account or the invocation, not by a config "
            "file). Run `claude mcp list` to see them.",
            len(missing),
            ", ".join(missing),
        )
    return missing


def _project_scope_mcp_servers(workspace_path: Path | None) -> dict[str, object]:
    if workspace_path is None:
        return {}
    servers = _mcp_servers_from_file(workspace_path / _MCP_JSON_FILENAME)
    declined = _declined_mcpjson_server_names(workspace_path)
    if not declined:
        return servers
    return {name: entry for name, entry in servers.items() if name not in declined}


def _declined_mcpjson_server_names(workspace_path: Path) -> frozenset[str]:
    entry = _user_config_project_entry(workspace_path)
    if entry is None:
        return frozenset()
    value = entry[0].get("disabledMcpjsonServers")
    if not isinstance(value, list):
        return frozenset()
    names = cast("list[object]", value)
    return frozenset(name for name in names if isinstance(name, str))


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
    if workspace_path is None:
        return {}
    entry = _user_config_project_entry(workspace_path)
    if entry is None:
        return {}
    return _mcp_servers_map(entry[0], entry[1])


def _user_config_project_entry(workspace_path: Path) -> tuple[dict[str, object], Path] | None:
    user_config_path = Path.home() / _USER_CONFIG_FILENAME
    config = _read_json_object(user_config_path)
    if config is None:
        return None
    projects = config.get("projects")
    if not isinstance(projects, dict):
        return None
    project_entries = cast("dict[str, object]", projects)
    for key in _workspace_config_keys(workspace_path):
        entry = project_entries.get(key)
        if isinstance(entry, dict):
            return cast("dict[str, object]", entry), user_config_path
    return None


def _workspace_config_keys(workspace_path: Path) -> tuple[str, ...]:
    literal = str(workspace_path)
    resolved = str(workspace_path.resolve())
    if resolved == literal:
        return (literal,)
    return (literal, resolved)


def _enabled_plugin_mcp_servers(workspace_path: Path | None) -> dict[str, object]:
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
    safe_plugin = _PLUGIN_ALIAS_UNSAFE.sub("_", plugin_name)
    safe_server = _PLUGIN_ALIAS_UNSAFE.sub("_", server_name)
    return f"plugin_{safe_plugin}_{safe_server}"


def _enabled_plugin_keys(workspace_path: Path | None) -> frozenset[str]:
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
    home_settings_dir = Path.home() / _CLAUDE_HOME_DIRNAME
    paths = [home_settings_dir / name for name in _SETTINGS_FILENAMES]
    if workspace_path is not None:
        workspace_settings_dir = workspace_path / _CLAUDE_HOME_DIRNAME
        paths.extend(workspace_settings_dir / name for name in _SETTINGS_FILENAMES)
    return tuple(paths)


def _installed_plugin_paths() -> dict[str, tuple[Path, ...]]:
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
    "claude_cli_mcp_server_lister",
    "claude_mcp_config",
    "claude_mcp_servers_ralph_cannot_proxy",
    "list_claude_cli_mcp_server_names",
    "load_existing_claude_upstream_servers",
    "parse_claude_mcp_list_names",
    "report_claude_mcp_servers_ralph_cannot_proxy",
    "reset_claude_mcp_proxy_report",
]
