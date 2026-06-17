"""Google Anti Gravity (AGY) transport helpers.

This module provides AGY-specific MCP transport helpers.

Research-confirmed facts:
- Executable: agy
- Print flag: --print
- Yolo flag: --dangerously-skip-permissions
- MCP config path: ~/.gemini/antigravity-cli/mcp_config.json
- HTTP JSON key: serverUrl
- Output format: plain text (not NDJSON) - uses JsonParserType.GENERIC

Ralph reads existing AGY upstream servers from the user config files at
~/.gemini/antigravity-cli/mcp_config.json and workspace .agents/mcp_config.json.
The agy_mcp_config() helper builds the AGY-native JSON payload for Ralph's MCP
endpoint using AGY's serverUrl field.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.transport.common import _load_mcpservers_from_paths, merge_existing_upstreams
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers

# AGY home config directory name within its default config root
_AGY_HOME_SUBDIR = "antigravity-cli"

# Process-local lock that serialises concurrent invocations of
# :func:`agy_workspace_mcp_endpoint` so two sibling AGY sessions cannot
# interleave their read/write/restore steps on the global MCP config
# file. See the context manager's docstring for the full concurrency
# contract.
_agy_mcp_lock = threading.Lock()


def _agy_global_config_path() -> Path:
    """Return AGY's global MCP config path.

    Measured behaviour: AGY's --print mode in a PTY only initialises its MCP
    client when this global config file exists; the workspace-level
    ``.agents/mcp_config.json`` file is not sufficient. The helper therefore
    writes the run-scoped Ralph entry here and restores the original contents
    on exit.
    """
    return Path.home() / ".gemini" / _AGY_HOME_SUBDIR / "mcp_config.json"


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


@contextmanager
def agy_workspace_mcp_endpoint(
    workspace_path: Path, endpoint: str, *, unsafe_mode: bool = False
) -> Iterator[None]:
    """Write a run-scoped Ralph MCP config to AGY's global path and restore it after exit.

    Concurrency safety: this context manager serialises concurrent callers
    with a single :class:`threading.Lock` and writes the merged config
    atomically (via ``os.replace``) so a parallel AGY session cannot
    observe a torn write or clobber a sibling session's restore step.
    The lock is process-local: it serialises within one Ralph process
    but does not block a separate AGY launch invoked by another
    process. Cross-process safety relies on the atomic replace below
    and on the original-bytes read happening INSIDE the critical
    section (so a parallel sibling cannot interleave its own
    write/restore between our read and our restore).
    """
    config_path = _agy_global_config_path()
    _agy_mcp_lock.acquire()
    try:
        original_bytes = config_path.read_bytes() if config_path.is_file() else None
        current_config: dict[str, object] = {
            "mcpServers": {RALPH_MCP_SERVER_NAME: {"serverUrl": endpoint}},
            "workspace_path": workspace_path,
        }
        merged_config = merge_existing_upstreams(
            "agy", current_config, unsafe_mode=unsafe_mode, workspace_path=workspace_path
        )
        config_payload = merged_config
        config_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: stage to a sibling temp file then Path.replace so a
        # concurrent reader (AGY Go runtime) never sees a half-written
        # config. The temp file is unique per call to avoid a TOCTOU
        # between two siblings staging the same name.
        _stage_path = config_path.with_suffix(
            config_path.suffix + f".ralph-staging.{id(config_payload)}"
        )
        _stage_path.write_text(json.dumps(config_payload, indent=2), encoding="utf-8")
        _stage_path.replace(config_path)
        try:
            yield
        finally:
            if original_bytes is None:
                if config_path.is_file():
                    config_path.unlink()
            else:
                # Atomic restore via the same staging pattern so a
                # concurrent sibling that staged its own write between
                # our yield and our restore does not lose its bytes.
                _restore_path = config_path.with_suffix(
                    config_path.suffix + f".ralph-restore.{id(original_bytes)}"
                )
                _restore_path.write_bytes(original_bytes)
                _restore_path.replace(config_path)
    finally:
        _agy_mcp_lock.release()


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
    if name == RALPH_MCP_SERVER_NAME:
        return None
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
    return normalize_upstream_mcp_servers(
        _load_mcpservers_from_paths(
            _agy_mcp_config_paths(workspace_path), _normalize_agy_server_entry
        )
    )


def _agy_mcp_config_paths(workspace_path: Path | None) -> tuple[Path, ...]:
    """Return the AGY MCP config file paths to check.

    Order: workspace-level .agents/mcp_config.json first (if workspace_path provided),
    then AGY's global config path (see ``_agy_global_config_path``).
    """
    workspace_paths: tuple[Path, ...] = ()
    if workspace_path is not None:
        workspace_paths = (workspace_path / ".agents" / "mcp_config.json",)
    return (
        *workspace_paths,
        _agy_global_config_path(),
    )


__all__ = [
    "agy_mcp_config",
    "agy_workspace_mcp_endpoint",
    "load_existing_agy_upstream_servers",
]
