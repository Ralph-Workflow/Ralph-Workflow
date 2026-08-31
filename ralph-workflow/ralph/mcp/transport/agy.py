"""Google Anti Gravity (AGY) transport helpers.

This module provides AGY-specific MCP transport helpers.

Research-confirmed facts:
- Executable: agy
- Print flag: --print
- Yolo flag: --dangerously-skip-permissions
- MCP config paths: ``~/.gemini/antigravity-cli/mcp_config.json`` (legacy/capability-listing path) AND ``~/.gemini/config/mcp_config.json`` (the path AGY's own bundled "agy-customizations" skill documents as "Global Configuration", and the one live dispatch actually reads -- see below).
- HTTP JSON key: serverUrl
- Print output: stream-json emits NDJSON; AgyParser is selected by transport.

Plan (Evidence Provenance, S-2), measured live against agy v1.1.10
(gemini-3.6-flash-low, --print --output-format stream-json), never guessed:
writing the Ralph entry to *only* ~/.gemini/antigravity-cli/mcp_config.json
makes AGY list the generic ``call_mcp_tool`` dispatcher in the ``init``
frame's ``tools`` array, but a live run instructed to use it explicitly
reported the dispatcher "not present in the current toolset" and never
opened a connection to Ralph's MCP server (zero request lines in
``mcp-server.log``, no wire-ledger record). Writing the identical merged
payload to ~/.gemini/config/mcp_config.json as well -- the path AGY's
bundled ``builtin/skills/agy-customizations/docs/mcp_servers.md`` documents
as the actual global MCP config -- made the same prompt produce a genuine
``call_mcp_tool`` invocation that reached Ralph's server and returned a real
tool result. Both paths are therefore kept in sync by this module: the first
for whatever legacy consumer still reads it, the second because it is the
one that makes live dispatch work.

Ralph reads existing AGY upstream servers from the user config files at
both global paths above and workspace .agents/mcp_config.json. The
agy_mcp_config() helper builds the AGY-native JSON payload for Ralph's MCP
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
from ralph.mcp.transport.config_overlay import (
    McpConfigOverlayLockTimeoutError,
    mcp_config_lock_path,
    mcp_config_overlay_lock,
    reclaim_config_overlay,
    restore_config_overlay,
    stage_config_overlay,
)
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers

# AGY home config directory name within its default config root
_AGY_HOME_SUBDIR = "antigravity-cli"

# Process-local lock that serialises concurrent invocations of
# :func:`agy_workspace_mcp_endpoint` so two sibling AGY sessions IN THIS
# PROCESS cannot interleave their read/write/restore steps on the global
# MCP config file. Cross-process contention is handled by the bounded
# advisory lock in :mod:`ralph.mcp.transport.config_overlay`; both layers
# are needed because ``flock`` serialises processes while the threading
# lock keeps the flock retry loop from racing two threads in the same
# process against one lock-file handle.
_agy_mcp_lock = threading.Lock()


class AgyMcpConfigLockTimeoutError(McpConfigOverlayLockTimeoutError):
    """The AGY global-MCP-config advisory lock could not be acquired in time.

    Raised fail-closed by :func:`agy_workspace_mcp_endpoint` when another
    process holds the overlay lock past ``_AGY_CONFIG_LOCK_TIMEOUT_SECONDS``.
    The run surfaces the timeout as a launch failure instead of racing the
    holder's write/restore steps and corrupting the shared config. It
    specialises the shared
    :class:`~ralph.mcp.transport.config_overlay.McpConfigOverlayLockTimeoutError`
    so callers may catch either the AGY-specific or the shared name.
    """


#: Bounded acquisition budget for the cross-process advisory lock. An
#: AGY overlay transaction is milliseconds long, so 10 s is far beyond
#: legitimate holder time while still failing closed fast enough that a
#: hung or crashed holder surfaces as an actionable launch error rather
#: than an unbounded stall. Read at call time so a test can shrink the
#: budget by assigning this module attribute.
_AGY_CONFIG_LOCK_TIMEOUT_SECONDS = 10.0


def _agy_global_config_path() -> Path:
    """Return AGY's legacy global MCP config path.

    Measured behaviour: AGY's --print mode in a PTY only initialises its MCP
    client when this global config file exists; the workspace-level
    ``.agents/mcp_config.json`` file is not sufficient. The helper therefore
    writes the run-scoped Ralph entry here and restores the original contents
    on exit. A live v1.1.10 capture (module docstring) showed this path alone
    is enough to make AGY *list* the ``call_mcp_tool`` dispatcher, but not
    enough to make dispatch actually work -- see :func:`_agy_secondary_config_path`.
    """
    return Path.home() / ".gemini" / _AGY_HOME_SUBDIR / "mcp_config.json"


def _agy_secondary_config_path() -> Path:
    """Return the global MCP config path AGY's own docs name and live dispatch reads.

    Measured behaviour (module docstring): a live v1.1.10 run that had the
    Ralph entry written *only* to :func:`_agy_global_config_path` advertised
    ``call_mcp_tool`` in its ``init`` frame but never actually reached
    Ralph's MCP server when instructed to use it. Writing the same entry
    here as well made the identical prompt produce a real ``tools/call``
    round trip. Kept as a separate seam (rather than folded into
    :func:`_agy_global_config_path`) so both paths stay independently
    monkeypatchable in tests.
    """
    return Path.home() / ".gemini" / "config" / "mcp_config.json"


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
    """Write a run-scoped Ralph MCP config to AGY's global paths and restore them after exit.

    Writes the identical merged payload to both
    :func:`_agy_global_config_path` and :func:`_agy_secondary_config_path`
    (module docstring: live dispatch needs the second path; some other
    AGY-side consumer may still read the first) and restores each
    independently on exit.

    Crash recovery: each overwrite is preceded by a durable
    ``<name>.ralph-backup`` record, and every transaction begins by
    reclaiming a record an earlier run was killed before it could apply
    (:func:`ralph.mcp.transport.config_overlay.reclaim_config_overlay`).
    An operator whose machine died mid-run gets their own MCP servers back
    on the next invocation instead of a permanently Ralph-only config file
    pointing at a dead port. The reclaim runs BEFORE the snapshot, so this
    run also cannot mistake an abandoned overlay for the operator's config.

    Concurrency safety: this context manager serialises concurrent callers
    with TWO layers: a process-local :class:`threading.Lock` for sibling
    threads in this process, and the bounded cross-process advisory lock
    in :mod:`ralph.mcp.transport.config_overlay` so two INDEPENDENT Ralph
    processes racing the same global config files serialize their
    read-stage-write/restore transactions instead of interleaving them.
    Both the original-bytes read and the restore happen INSIDE the
    critical section, so a racing process cannot observe a torn write or
    clobber a sibling's restore step. The advisory lock is bounded
    (``_AGY_CONFIG_LOCK_TIMEOUT_SECONDS``) and fails closed with
    :class:`AgyMcpConfigLockTimeoutError` rather than hanging the launch
    path; the config writes themselves stay atomic via ``os.replace``.
    """
    config_paths = (_agy_global_config_path(), _agy_secondary_config_path())
    lock_path = mcp_config_lock_path(config_paths[0])
    _agy_mcp_lock.acquire()
    try:
        with mcp_config_overlay_lock(
            lock_path,
            timeout_seconds=_AGY_CONFIG_LOCK_TIMEOUT_SECONDS,
            error_type=AgyMcpConfigLockTimeoutError,
        ):
            for path in config_paths:
                reclaim_config_overlay(path)

            current_config: dict[str, object] = {
                "mcpServers": {RALPH_MCP_SERVER_NAME: {"serverUrl": endpoint}},
                "workspace_path": workspace_path,
            }
            merged_config = merge_existing_upstreams(
                "agy", current_config, unsafe_mode=unsafe_mode, workspace_path=workspace_path
            )
            config_payload = json.dumps(merged_config, indent=2).encode("utf-8")
            original_bytes_by_path = {
                path: stage_config_overlay(path, config_payload) for path in config_paths
            }
            try:
                yield
            finally:
                for path in config_paths:
                    restore_config_overlay(path, original_bytes_by_path[path])
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

    Order: workspace-level .agents/mcp_config.json first (if workspace_path
    provided), then both of AGY's global config paths (see
    ``_agy_global_config_path`` and ``_agy_secondary_config_path``) so a
    server a user configured through either surface is not dropped by an
    unsafe-mode merge.
    """
    workspace_paths: tuple[Path, ...] = ()
    if workspace_path is not None:
        workspace_paths = (workspace_path / ".agents" / "mcp_config.json",)
    return (
        *workspace_paths,
        _agy_global_config_path(),
        _agy_secondary_config_path(),
    )


__all__ = [
    "AgyMcpConfigLockTimeoutError",
    "agy_mcp_config",
    "agy_workspace_mcp_endpoint",
    "load_existing_agy_upstream_servers",
]
