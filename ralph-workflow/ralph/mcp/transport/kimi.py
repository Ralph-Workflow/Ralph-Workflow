"""Kimi Code CLI transport helpers.

This module provides Kimi Code-specific MCP transport helpers.

Research-confirmed facts (Kimi Code CLI ``kimi``, v0.36.x):

* Executable: ``kimi`` (binary name on ``PATH``)
* Headless flag: ``-p <prompt>`` with ``--output-format stream-json``
  (the documented long form ``--output-format=stream-json`` is accepted
  as well; ``--print``/``--afk`` are NOT options on this binary, and
  ``--yolo``/``--auto``/``--plan`` conflict with ``--prompt``)
* MCP config paths (documented precedence, project over user):
  ``<cwd>/.kimi-code/mcp.json`` and the user-global
  ``$KIMI_CODE_HOME/mcp.json`` (defaulting to ``~/.kimi-code/mcp.json``).
  Kimi also reads a project-root ``.mcp.json``, but Ralph deliberately
  leaves that file alone because it is a cross-tool convention (other
  agents use it too) and the two ``.kimi-code`` paths are sufficient to
  wire MCP for any invocation pattern.
* Project-level trust gate (measured on v0.36.1, not guessable): headless
  ``kimi -p`` silently IGNORES the workspace ``.kimi-code/mcp.json`` when
  the folder is not in the user's trusted-workspace store -- no error, no
  warning, the MCP tools simply never register (the TUI shows a
  trust prompt instead; headless drops the server).  The user-global
  ``$KIMI_CODE_HOME/mcp.json`` has NO trust gate and works in every
  headless session, so the run-scoped write MUST target the global path
  even when the workspace path is deliberately left alone.
  Ralph therefore writes the merged run-scoped config to the global path
  ALWAYS and to the workspace path ONLY when that file already exists
  (a pre-existing workspace config implies the operator has already
  trusted the folder and manages their own project-level MCP surface).
* HTTP JSON key: ``url`` (Kimi Code's documented MCP server shape)
* Output format: NDJSON ``stream-json`` Message frames keyed by
  ``role`` (parsed by ``KimiParser``)

Kimi Code's MCP server configuration uses the standard MCP convention::

    {
        "mcpServers": {
            "ralph": {
                "url": "http://127.0.0.1:<port>/mcp"
            }
        }
    }

Ralph reads existing Kimi upstream servers from the workspace-local
``.kimi-code/mcp.json`` and the user-global ``~/.kimi-code/mcp.json``
files, merges the run-scoped ``ralph`` entry through the existing
upstream merge flow (``merge_existing_upstreams``), and writes the
merged config to the user-global path always (no trust gate) plus the
workspace path only when it already exists.

The write/restore protocol is the shared one in
``ralph/mcp/transport/config_overlay.py``: a process-local
``threading.Lock`` for sibling threads, a bounded cross-process advisory
lock for sibling PROCESSES, a durable ``mcp.json.ralph-backup`` record
written before the overwrite so a killed run self-heals on the next
invocation, and an atomic ``Path.replace`` that keeps every publication
torn-write-safe.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.transport.common import _load_mcpservers_from_paths, merge_existing_upstreams
from ralph.mcp.transport.config_overlay import (
    mcp_config_lock_path,
    mcp_config_overlay_lock,
    reclaim_config_overlay,
    restore_config_overlay,
    stage_config_overlay,
)
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers

# Process-local lock that serialises concurrent invocations of
# :func:`kimi_workspace_mcp_endpoint` so two sibling Kimi sessions IN
# THIS PROCESS cannot interleave their read/write/restore steps on the
# global MCP config file.  Cross-process contention is handled by the
# bounded advisory lock below; both layers are needed because the
# advisory lock serialises processes while the threading lock keeps the
# retry loop from racing two threads in this process against one
# lock-file handle.  See the context manager's docstring for the full
# concurrency contract.
_kimi_mcp_lock = threading.Lock()

#: Bounded acquisition budget for the cross-process advisory lock that
#: guards Kimi Code's user-global MCP config.  Read at call time so a
#: test can shrink the budget by assigning this module attribute.
_KIMI_CONFIG_LOCK_TIMEOUT_SECONDS = 10.0


def _kimi_global_config_path() -> Path:
    """Return Kimi Code's global MCP config path.

    The documented global MCP config surface is
    ``$KIMI_CODE_HOME/mcp.json``, defaulting to ``~/.kimi-code/mcp.json``
    when the environment variable is unset or empty.
    """
    kimi_home = os.environ.get("KIMI_CODE_HOME", "")
    home = Path(kimi_home) if kimi_home else Path.home() / ".kimi-code"
    return home / "mcp.json"


def _kimi_workspace_config_path(workspace_path: Path) -> Path:
    """Return the workspace-local Kimi Code MCP config path.

    The documented workspace-local MCP config surface is
    ``.kimi-code/mcp.json`` (relative to the workspace root).
    """
    return workspace_path / ".kimi-code" / "mcp.json"


def kimi_mcp_config(endpoint: str) -> str:
    """Return the Kimi Code MCP JSON config string pointing to the given endpoint.

    Args:
        endpoint: The MCP server HTTP endpoint URL.

    Returns:
        JSON string with ``mcpServers`` containing the Ralph entry with
        the ``url`` key (Kimi Code's documented MCP server shape).
    """
    config_payload = {
        "mcpServers": {
            RALPH_MCP_SERVER_NAME: {
                "url": endpoint,
            }
        }
    }
    return json.dumps(config_payload, separators=(",", ":"))


def _kimi_paths_to_consider(
    workspace_path: Path | None,
) -> tuple[Path, ...]:
    """Return the list of Kimi Code MCP config paths to consider.

    Order: the user-global ``$KIMI_CODE_HOME/mcp.json`` first (always),
    then the workspace-local ``.kimi-code/mcp.json`` (when
    ``workspace_path`` is provided).  The order matters for
    :func:`_load_mcpservers_from_paths`, whose dict-update merge gives
    the LAST path precedence on same-name collisions — Kimi Code's
    documented precedence is project-level over user-level, so the
    workspace-local file must come last.  The write/restore side is
    order-independent (the same merged payload is written to every
    path and each path's original bytes are restored independently).
    """
    workspace_paths: tuple[Path, ...] = ()
    if workspace_path is not None:
        workspace_paths = (_kimi_workspace_config_path(workspace_path),)
    return (
        _kimi_global_config_path(),
        *workspace_paths,
    )


def _kimi_write_target_paths(workspace_path: Path | None) -> tuple[Path, ...]:
    """Return the paths the run-scoped merged config is written to.

    The user-global ``$KIMI_CODE_HOME/mcp.json`` is ALWAYS a write target:
    it carries no workspace-trust gate, so a headless ``kimi -p`` session
    registers its MCP tools in every workspace.  The workspace-local
    ``.kimi-code/mcp.json`` is a write target ONLY when it already exists:
    measured on v0.36.1, headless mode silently drops project-level MCP
    servers from untrusted folders, so creating the file for a workspace
    the operator has not trusted would write config the CLI then ignores
    (and would fabricate a project-level surface the operator never made).
    """
    targets: list[Path] = [_kimi_global_config_path()]
    if workspace_path is not None:
        workspace_config = _kimi_workspace_config_path(workspace_path)
        if workspace_config.is_file():
            targets.append(workspace_config)
    return tuple(targets)


@contextmanager
def kimi_workspace_mcp_endpoint(
    workspace_path: Path, endpoint: str, *, unsafe_mode: bool = False
) -> Iterator[None]:
    """Write a run-scoped Ralph MCP config to Kimi Code's paths and restore them on exit.

    Writes the merged config (Ralph entry + merged upstream servers in
    ``unsafe_mode``) to the user-global ``$KIMI_CODE_HOME/mcp.json``
    ALWAYS (no trust gate) and to the workspace-local
    ``.kimi-code/mcp.json`` ONLY when that file already exists -- headless
    ``kimi -p`` silently ignores project-level MCP config in untrusted
    folders, so a newly created workspace file would never be honored.
    On exit the original bytes are restored on each path that was
    modified.

    Crash recovery: each overwrite is preceded by a durable
    ``<name>.ralph-backup`` record, and every transaction begins by
    reclaiming a record an earlier run was killed before it could apply
    (:func:`ralph.mcp.transport.config_overlay.reclaim_config_overlay`).
    An operator whose machine died mid-run gets their own MCP servers back
    on the next invocation instead of a permanently Ralph-only config file
    pointing at a dead port.  The reclaim runs BEFORE the snapshot, so this
    run also cannot mistake an abandoned overlay for the operator's config.

    Concurrency safety: callers are serialised by TWO layers -- a
    process-local :class:`threading.Lock` for sibling threads, and the
    bounded cross-process advisory lock in
    :mod:`ralph.mcp.transport.config_overlay` so two INDEPENDENT Ralph
    processes cannot interleave their snapshot/write/restore steps on the
    shared user-global file.  Both the original-bytes read and the restore
    happen INSIDE the critical section.  The advisory lock is bounded
    (``_KIMI_CONFIG_LOCK_TIMEOUT_SECONDS``) and fails closed with
    :class:`~ralph.mcp.transport.config_overlay.McpConfigOverlayLockTimeoutError`
    rather than hanging the launch path.
    """
    lock_path = mcp_config_lock_path(_kimi_global_config_path())
    _kimi_mcp_lock.acquire()
    try:
        with mcp_config_overlay_lock(
            lock_path, timeout_seconds=_KIMI_CONFIG_LOCK_TIMEOUT_SECONDS
        ):
            # Reclaim before resolving the write targets: an abandoned
            # overlay is undone first so the target set and the snapshot
            # both reflect the operator's own config, not the corpse a
            # killed run left behind.
            for config_path in _kimi_paths_to_consider(workspace_path):
                reclaim_config_overlay(config_path)
            write_targets = _kimi_write_target_paths(workspace_path)

            current_config: dict[str, object] = {
                "mcpServers": {RALPH_MCP_SERVER_NAME: {"url": endpoint}},
                "workspace_path": workspace_path,
            }
            merged_config = merge_existing_upstreams(
                "kimi", current_config, unsafe_mode=unsafe_mode, workspace_path=workspace_path
            )
            config_payload = json.dumps(merged_config, indent=2).encode("utf-8")
            original_bytes_by_path = {
                config_path: stage_config_overlay(config_path, config_payload)
                for config_path in write_targets
            }
            try:
                yield
            finally:
                for config_path in write_targets:
                    restore_config_overlay(config_path, original_bytes_by_path[config_path])
    finally:
        _kimi_mcp_lock.release()


def _normalize_kimi_server_entry(name: str, entry: object) -> tuple[str, object] | None:
    """Normalize a Kimi Code server entry to Ralph's expected format.

    Kimi Code's MCP server shape uses ``url`` for HTTP servers, which is
    the standard Ralph normalizer's expected key.  This helper is the
    identity mapping for kimi (kept as a normalizer hook for parity
    with the agy / claude / cursor helpers, and as the documented
    extension point if a future Kimi release uses a different key).

    Args:
        name: Server name.
        entry: Raw server entry dict from ``mcpServers``.

    Returns:
        Tuple of ``(name, normalized_entry)`` if valid, ``None`` if
        skipped.
    """
    if name == RALPH_MCP_SERVER_NAME:
        return None
    if not isinstance(entry, Mapping):
        return None
    return name, cast("dict[str, object]", entry)


def load_existing_kimi_upstream_servers(
    workspace_path: Path | None = None,
) -> tuple[UpstreamMcpServer, ...]:
    """Read Kimi Code's MCP config files and return any upstream MCP servers found.

    Args:
        workspace_path: Optional workspace path for the workspace-local
            ``.kimi-code/mcp.json``.

    Returns:
        Tuple of :class:`UpstreamMcpServer` objects found in Kimi Code
        config files.  The Ralph entry is filtered out so it does not
        collide with the run-scoped ``ralph`` injection.
    """
    return normalize_upstream_mcp_servers(
        _load_mcpservers_from_paths(
            _kimi_paths_to_consider(workspace_path),
            _normalize_kimi_server_entry,
        )
    )


__all__ = [
    "kimi_mcp_config",
    "kimi_workspace_mcp_endpoint",
    "load_existing_kimi_upstream_servers",
]
