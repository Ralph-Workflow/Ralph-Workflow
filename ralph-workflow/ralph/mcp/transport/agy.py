"""Google Anti Gravity (AGY) transport helpers.

This module provides AGY-specific MCP transport helpers following the Codex pattern
of isolated temp-dir home directory isolation (never mutates user's live config).

Research-confirmed facts (Step 1):
- Executable: agy
- Print flag: --print
- Yolo flag: --dangerously-skip-permissions
- Session flag: --conversation {}
- MCP config path: ~/.gemini/antigravity-cli/mcp_config.json
- HTTP JSON key: serverUrl (confirmed)
- Home env var: GEMINI_HOME (expected; not fully confirmed by research - limitation noted)
- Output format: plain text (not NDJSON) - uses JsonParserType.GENERIC

Known limitation: GEMINI_HOME env var override is expected based on the pattern used
by similar tools but was not explicitly confirmed in AGY documentation. If AGY ignores
this env var in a future release, the isolation design will regress. The temp dir is
still created to avoid mutating user config, but the MCP endpoint injection may not
be honored by AGY.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers

# AGY home config directory name within GEMINI_HOME
_AGY_HOME_SUBDIR = "antigravity-cli"


def agy_mcp_config(endpoint: str) -> str:
    """Return the AGY MCP JSON config string pointing to the given endpoint.

    Args:
        endpoint: The MCP server HTTP endpoint URL.

    Returns:
        JSON string with mcpServers containing the Ralph entry with serverUrl key.
    """
    config_payload = {
        "mcpServers": {
            RALPH_MCP_SERVER_NAME: {
                "serverUrl": endpoint,
            }
        }
    }
    return json.dumps(config_payload, separators=(",", ":"))


def _normalize_agy_server_entry(name: str, entry: object) -> tuple[str, object] | None:
    """Normalize an AGY server entry to Ralph's expected format.

    AGY uses 'serverUrl' for HTTP servers; Ralph's normalize_upstream_mcp_servers
    expects 'url'. This helper converts 'serverUrl' -> 'url' so the standard
    normalizer can process AGY config entries.

    Args:
        name: Server name.
        entry: Raw server entry dict from mcpServers.

    Returns:
        Tuple of (name, normalized_entry) if valid, None if skipped.
    """
    if not isinstance(entry, Mapping):
        return None
    casted = cast("dict[str, object]", entry)
    # AGY uses serverUrl; Ralph normalizer expects url
    if "serverUrl" in casted and "url" not in casted:
        casted = {**casted, "url": casted["serverUrl"]}
    return name, casted


def load_existing_agy_upstream_servers(
    workspace_path: Path | None = None,
) -> tuple[UpstreamMcpServer, ...]:
    """Read AGY's MCP config files and return any upstream MCP servers found.

    Args:
        workspace_path: Optional workspace path for workspace-level AGY config.

    Returns:
        Tuple of UpstreamMcpServer objects found in AGY config files.
    """
    merged: dict[str, object] = {}
    for path in _agy_mcp_config_paths(workspace_path):
        config_obj = _parse_json_config_file(path)
        if not config_obj:
            continue
        value = config_obj.get("mcpServers")
        if isinstance(value, dict):
            for srv_name, srv_entry in value.items():
                normalized = _normalize_agy_server_entry(srv_name, srv_entry)
                if normalized is not None:
                    merged[normalized[0]] = normalized[1]
    return normalize_upstream_mcp_servers(merged)


def _agy_mcp_config_paths(workspace_path: Path | None) -> tuple[Path, ...]:
    """Return the AGY MCP config file paths to check.

    Order: workspace-level .agents/mcp_config.json first (if workspace_path provided),
    then global ~/.gemini/antigravity-cli/mcp_config.json.
    """
    workspace_paths: tuple[Path, ...] = ()
    if workspace_path is not None:
        workspace_paths = (
            workspace_path / ".agents" / "mcp_config.json",
        )
    return (
        *workspace_paths,
        Path.home() / ".gemini" / _AGY_HOME_SUBDIR / "mcp_config.json",
    )


def _parse_json_config_file(path: Path) -> dict[str, object]:
    """Parse a JSON config file, returning empty dict on error."""
    if not path.exists():
        return {}
    try:
        raw_payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw_payload, dict):
        return {}
    return cast("dict[str, object]", raw_payload)


def prepare_agy_home(
    endpoint: str | None,
    *,
    workspace_path: Path | None,
    existing_home: str | None,
) -> tuple[str, tuple[UpstreamMcpServer, ...]]:
    """Prepare an isolated AGY home directory and return its path with upstream servers.

    This ALWAYS creates an isolated temp dir (never writes to user's real config),
    following the Codex pattern.

    Args:
        endpoint: Optional Ralph MCP endpoint URL to inject.
        workspace_path: Optional workspace path for workspace-level config.
        existing_home: Optional existing AGY home to mirror (e.g., from GEMINI_HOME env).

    Returns:
        Tuple of (path to isolated temp dir, upstream servers from existing config).
    """
    agy_home = _allocate_agy_home_dir(workspace_path)
    agy_home.mkdir(parents=True, exist_ok=True)

    # Mirror existing home contents to temp dir
    source_home = (
        Path(existing_home).expanduser() / _AGY_HOME_SUBDIR
        if existing_home
        else Path.home() / ".gemini" / _AGY_HOME_SUBDIR
    )
    if source_home.exists():
        _mirror_agy_home(source_home, agy_home / _AGY_HOME_SUBDIR)

    source_config = source_home / "mcp_config.json"
    upstreams: tuple[UpstreamMcpServer, ...] = ()
    if source_config.exists():
        config_obj = _parse_json_config_file(source_config)
        mcp_servers = config_obj.get("mcpServers")
        if isinstance(mcp_servers, dict):
            upstreams = normalize_upstream_mcp_servers(cast("dict[str, object]", mcp_servers))

    # Write merged config with Ralph endpoint if provided
    if endpoint:
        dest_config = agy_home / _AGY_HOME_SUBDIR / "mcp_config.json"
        dest_config.parent.mkdir(parents=True, exist_ok=True)
        merged_obj: dict[str, object] = {"mcpServers": {}}
        if source_config.exists():
            existing = _parse_json_config_file(source_config)
            if "mcpServers" in existing:
                merged_obj["mcpServers"] = cast("dict[str, object]", existing["mcpServers"])
        # Add Ralph entry
        mcp_servers = cast("dict[str, object]", merged_obj["mcpServers"])
        mcp_servers[RALPH_MCP_SERVER_NAME] = {"serverUrl": endpoint}
        dest_config.write_text(json.dumps(merged_obj, separators=(",", ":")), encoding="utf-8")

    return str(agy_home), upstreams


def _allocate_agy_home_dir(workspace_path: Path | None) -> Path:
    """Allocate a temp dir for AGY home, preferring workspace .agent/tmp."""
    if workspace_path is None:
        return Path(tempfile.mkdtemp(prefix="ralph-agy-home-"))

    tmp_root = workspace_path / ".agent" / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="agy-home-", dir=str(tmp_root)))


def _mirror_agy_home(source_home: Path, dest_home: Path) -> None:
    """Mirror source home directory contents to destination (symlinks with copy fallback)."""
    if not source_home.exists():
        return
    dest_home.mkdir(parents=True, exist_ok=True)
    for entry in source_home.iterdir():
        if entry.name == "mcp_config.json":
            # Don't copy mcp_config.json - we'll regenerate it with Ralph's endpoint
            continue
        destination = dest_home / entry.name
        try:
            destination.symlink_to(entry, target_is_directory=entry.is_dir())
        except OSError:
            if entry.is_dir():
                shutil.copytree(entry, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(entry, destination)


__all__ = [
    "agy_mcp_config",
    "load_existing_agy_upstream_servers",
    "prepare_agy_home",
]
