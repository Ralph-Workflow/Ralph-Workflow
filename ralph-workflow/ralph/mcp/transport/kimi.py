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
merged config to BOTH paths so the agent picks up MCP regardless of
the cwd it was launched from.

The write/restore protocol mirrors the Cursor pattern in
``ralph/mcp/transport/cursor.py``: a process-local ``threading.Lock``
serialises concurrent sessions, an atomic ``Path.replace`` keeps the
write torn-write-safe, and the original-bytes restore happens INSIDE
the critical section so a parallel sibling cannot interleave its own
write/restore between our read and our restore.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import cast

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import atomic_write_bytes_if_changed
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.transport.common import _load_mcpservers_from_paths, merge_existing_upstreams
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers

# Process-local lock that serialises concurrent invocations of
# :func:`kimi_workspace_mcp_endpoint` so two sibling Kimi sessions
# cannot interleave their read/write/restore steps on the global MCP
# config file.  See the context manager's docstring for the full
# concurrency contract.
_kimi_mcp_lock = threading.Lock()


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


def _prepare_config_parent(path: Path) -> None:
    """Create a config parent only when a changed publication needs it."""
    DEFAULT_FILE_BACKEND.mkdir(path, parents=True, exist_ok=True)


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


@contextmanager
def kimi_workspace_mcp_endpoint(
    workspace_path: Path, endpoint: str, *, unsafe_mode: bool = False
) -> Iterator[None]:
    """Write a run-scoped Ralph MCP config to Kimi Code's paths and restore them on exit.

    Writes the merged config (Ralph entry + merged upstream servers in
    ``unsafe_mode``) to BOTH the workspace-local ``.kimi-code/mcp.json``
    and the user-global ``$KIMI_CODE_HOME/mcp.json`` so a Kimi invocation
    launched from inside or outside the workspace picks up the run-scoped
    Ralph MCP endpoint.  On exit the original bytes are restored on each
    path that was modified.

    Concurrency safety: this context manager serialises concurrent callers
    with a single :class:`threading.Lock` (process-local) and writes the
    merged config atomically (via ``Path.replace``) so a parallel Kimi
    session cannot observe a torn write or clobber a sibling session's
    restore step.  The original-bytes read happens INSIDE the critical
    section so a parallel sibling cannot interleave its own write/restore
    between our read and our restore.
    """
    _kimi_mcp_lock.acquire()
    try:
        # Snapshot the original bytes BEFORE we write so the restore step
        # can put each path back exactly as we found it (including the
        # missing-file case for paths that did not exist).
        original_by_path: dict[Path, bytes | None] = {}
        for config_path in _kimi_paths_to_consider(workspace_path):
            original_by_path[config_path] = (
                config_path.read_bytes() if config_path.is_file() else None
            )

        current_config: dict[str, object] = {
            "mcpServers": {RALPH_MCP_SERVER_NAME: {"url": endpoint}},
            "workspace_path": workspace_path,
        }
        merged_config = merge_existing_upstreams(
            "kimi", current_config, unsafe_mode=unsafe_mode, workspace_path=workspace_path
        )

        try:
            config_payload = json.dumps(merged_config, indent=2).encode("utf-8")
            for config_path in _kimi_paths_to_consider(workspace_path):
                # Atomically publish only changed bytes. The primitive avoids
                # staging/replacing an unchanged effective config and defers
                # parent creation until a changed publish requires it.
                atomic_write_bytes_if_changed(
                    DEFAULT_FILE_BACKEND,
                    config_path,
                    config_payload,
                    tmp_path=config_path.with_suffix(config_path.suffix + ".ralph-staging"),
                    prepare_write=partial(_prepare_config_parent, config_path.parent),
                )
            yield
        finally:
            for config_path in _kimi_paths_to_consider(workspace_path):
                original_bytes = original_by_path.get(config_path)
                if original_bytes is None:
                    if config_path.is_file():
                        config_path.unlink()
                else:
                    # Restore atomically while avoiding a no-op replace when
                    # the pre-run bytes are already back in place.
                    atomic_write_bytes_if_changed(
                        DEFAULT_FILE_BACKEND,
                        config_path,
                        original_bytes,
                        tmp_path=config_path.with_suffix(config_path.suffix + ".ralph-restore"),
                        prepare_write=partial(_prepare_config_parent, config_path.parent),
                    )
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
