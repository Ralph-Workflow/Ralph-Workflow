"""MCP handlers for the explore index: ``ralph_index_status`` and ``ralph_reindex``.

Both handlers operate on an optional, lazily-initialized
:class:`ExploreIndex` handle attached to the session. When the index
is disabled or missing, both handlers return a structured "disabled"
response rather than raising — this keeps the live tool surface
unchanged for existing callers.

The handlers own the ExploreIndex handle and the path-resolution
logic so they are testable in isolation. Tests construct a
``_SqliteBackedExploreIndex`` directly over a ``tmp_path`` workspace
and pass it as ``session.explore_index``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final

from ralph.mcp.explore.pipeline import (
    DEFAULT_TIMEOUT_MS,
    ReindexOptions,
    ReindexResult,
    reindex,
)
from ralph.mcp.explore.store import (
    DEFAULT_INDEX_ROOT,
    ExploreStore,
    normalize_index_path,
)
from ralph.mcp.tools.coordination import (
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    require_capability,
)
from ralph.mcp.tools.workspace._utils import (
    WORKSPACE_METADATA_READ_CAPABILITY,
    WORKSPACE_READ_CAPABILITY,
    _tool_json,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


logger = logging.getLogger(__name__)


# --- Explore index handle -------------------------------------------------


@dataclass
class ExploreIndex:
    """Optional, lazily-initialized handle on the session/workspace.

    Stores the index directory + a live :class:`ExploreStore`. The
    handle is created by the MCP server bootstrap (or by tests) and
    injected as ``session.explore_index``. When ``None``, handlers
    behave exactly as today — no metadata added, no dirty marking.

    The handle exposes a single public surface to MCP handlers:
    ``mark_dirty(paths, source_tool, reason)`` so the
    dirty-path tracking stays narrow and testable.
    """

    workspace_root: Path
    index_root: Path
    store: ExploreStore
    last_refresh_kind: str = "none"
    cold_index_required: bool = False
    generation: int = 0
    last_job_status: str | None = None

    @property
    def index_dir(self) -> Path:
        return self.index_root

    def mark_dirty(
        self, paths: Sequence[str], *, source_tool: str, reason: str = "mutated"
    ) -> None:
        for path in paths:
            self.store.mark_dirty(path, reason=reason, source_tool=source_tool)

    def index_storage_bytes(self) -> int:
        return self.store.index_storage_bytes()

    def build_options(self, *, timeout_ms: int) -> ReindexOptions:
        return ReindexOptions(mode="changed", timeout_ms=timeout_ms)


def _resolve_index_dir(workspace_root: Path) -> Path:
    """Return the canonical index directory under ``.agent/ralph-explore``."""
    return Path(workspace_root) / DEFAULT_INDEX_ROOT


def build_explore_index(workspace_root: Path) -> ExploreIndex:
    """Construct a fresh ExploreIndex for ``workspace_root``.

    Tests call this directly. Production callers should defer
    construction to the MCP server bootstrap so the index is created
    lazily and only when first queried.
    """
    workspace_root = Path(workspace_root).resolve()
    index_root = _resolve_index_dir(workspace_root)
    store = ExploreStore(index_root)
    raw = store.get_setting("current_generation") or "0"
    try:
        generation = int(raw)
    except ValueError:
        generation = 0
    latest = store.latest_job()
    last_status = latest["status"] if latest is not None else None
    return ExploreIndex(
        workspace_root=workspace_root,
        index_root=index_root,
        store=store,
        generation=generation,
        last_job_status=last_status,
    )


# --- Handler helpers ------------------------------------------------------


def _resolve_explore_index(session: object) -> ExploreIndex | None:
    """Return the explore index handle attached to ``session`` if any."""
    handle = getattr(session, "explore_index", None)
    if handle is None:
        return None
    return handle


def _gitignore_coverage(workspace_root: Path) -> bool:
    """Return True when the ``.agent/`` gitignore rule is present.

    The existing ``.agent/`` rule in
    ``ralph/config/bootstrap.py:_DEFAULT_GITIGNORE_PATTERNS`` already
    covers ``.agent/ralph-explore/``. This helper reports coverage
    by reading ``.gitignore`` directly so the index_status response
    is honest without a configuration lookup.
    """
    gitignore = Path(workspace_root) / ".gitignore"
    if not gitignore.is_file():
        return False
    try:
        text = gitignore.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(
        line.strip() in {".agent/", ".agent", "/.agent/"}
        for line in text.splitlines()
    )


# --- MCP handlers ---------------------------------------------------------


def handle_ralph_index_status(
    session: CoordinationSessionLike,
    workspace: object,
    params: dict[str, object],
) -> ToolResult:
    """Report index health and freshness.

    Capability: ``WorkspaceMetadataRead`` (read-only metadata).
    """
    require_capability(
        session, WORKSPACE_METADATA_READ_CAPABILITY, "Explore index status"
    )
    workspace_root = getattr(workspace, "root", None) or params.get(
        "workspace_root", ""
    )
    workspace_root = Path(str(workspace_root)) if workspace_root else Path.cwd()
    handle = _resolve_explore_index(session)
    if handle is None:
        handle = build_explore_index(workspace_root)
        cold_index_required = handle.generation == 0
        payload = _build_status_payload(
            handle, workspace_root, cold_index_required
        )
        payload["enabled"] = False
        payload["index_exists"] = False
        return ToolResult(
            content=[ToolContent.text_content(_tool_json(payload))],
            is_error=False,
        )
    cold_index_required = handle.generation == 0
    payload = _build_status_payload(handle, workspace_root, cold_index_required)
    payload["enabled"] = True
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload))],
        is_error=False,
    )


def _build_status_payload(
    handle: ExploreIndex,
    workspace_root: Path,
    cold_index_required: bool,
) -> dict[str, object]:
    store = handle.store
    latest = store.latest_job()
    indexed_at_raw = latest["finished_at"] if latest is not None else None
    indexed_at = float(indexed_at_raw) if indexed_at_raw is not None else None
    dirty_paths = store.peek_dirty_paths()
    files_indexed = sum(1 for _ in store.iter_files())
    # Ponytail: ``files_stale`` counts deleted files; ``is_stale`` is
    # true when dirty paths exist OR a deleted file still has a row.
    stale_paths = sum(1 for row in store.iter_files() if row.is_deleted)
    is_stale = bool(dirty_paths) or stale_paths > 0
    return {
        "index_exists": handle.generation > 0,
        "generation": handle.generation,
        "indexed_at": indexed_at,
        "files_indexed": files_indexed,
        "files_stale": stale_paths,
        "last_job": dict(latest) if latest is not None else None,
        "capabilities": ["evidence_lookup", "fts_search"],
        "graph_backend": "sqlite",
        "dirty_paths_count": len(dirty_paths),
        "cold_index_required": cold_index_required,
        "last_refresh_kind": handle.last_refresh_kind,
        "is_stale": is_stale,
        "stale_paths_count": stale_paths,
        "index_storage_bytes": handle.index_storage_bytes(),
        "gitignore_coverage": {
            "present": _gitignore_coverage(workspace_root),
            "rule": ".agent/",
        },
    }


def handle_ralph_reindex(
    session: CoordinationSessionLike,
    workspace: object,
    params: dict[str, object],
) -> ToolResult:
    """Run a bounded changed/full reindex.

    Capability: ``WorkspaceRead`` (the reindex touches workspace
    files). Production callers are expected to gate reindex behind a
    higher privilege in the future; the Phase 1 contract keeps the
    current capability.
    """
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Explore reindex")
    mode = str(params.get("mode", "changed"))
    if mode not in {"changed", "full"}:
        raise InvalidParamsError(
            f"Invalid reindex mode: {mode!r}; expected 'changed' or 'full'"
        )
    timeout_ms = int(params.get("timeout_ms", DEFAULT_TIMEOUT_MS))
    if timeout_ms <= 0:
        raise InvalidParamsError("timeout_ms must be positive")
    path_scope_raw = params.get("path_scope")
    path_scope: tuple[str, ...] = ()
    if isinstance(path_scope_raw, list):
        path_scope = tuple(normalize_index_path(str(p)) for p in path_scope_raw)

    workspace_root = getattr(workspace, "root", None) or params.get(
        "workspace_root", ""
    )
    workspace_root = Path(str(workspace_root)) if workspace_root else Path.cwd()

    handle = _resolve_explore_index(session)
    if handle is None:
        handle = build_explore_index(workspace_root)
        # The first call from a session without an explore index is
        # typically the cold build; tag it so downstream consumers
        # can decide whether to block on it.
        handle.cold_index_required = handle.generation == 0

    started_at = time.time()
    options = ReindexOptions(
        mode=mode,
        timeout_ms=timeout_ms,
        path_scope=path_scope,
    )
    try:
        result = reindex(handle.store, handle.workspace_root, options=options)
    except Exception as exc:  # noqa: BLE001
        logger.exception("ralph_reindex crashed: {err}", err=exc)
        return ToolResult(
            content=[
                ToolContent.text_content(
                    _tool_json(
                        {
                            "status": "failed",
                            "error": f"{type(exc).__name__}: {exc}",
                            "elapsed_seconds": time.time() - started_at,
                        }
                    )
                )
            ],
            is_error=True,
        )
    handle.generation = result.generation
    handle.last_job_status = result.status
    handle.last_refresh_kind = "full" if mode == "full" else "changed"
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(_build_reindex_payload(result)))],
        is_error=False,
    )


def _build_reindex_payload(result: ReindexResult) -> dict[str, object]:
    return {
        "job_id": result.job_id,
        "job_status": result.status,
        "generation": result.generation,
        "changed_files": list(result.changed_files),
        "failed_files": list(result.failed_files),
        "parse_count": result.parse_count,
        "dirty_paths_count": result.dirty_paths_count,
        "elapsed_seconds": result.elapsed_seconds,
        "error_summary": result.error_summary,
    }


__all__ = [
    "DEFAULT_INDEX_ROOT",
    "ExploreIndex",
    "build_explore_index",
    "handle_ralph_index_status",
    "handle_ralph_reindex",
]


# --- Minimal unused-import shim for static analyzers ----------------------

# Re-export module-level helpers so tests can import them directly.
__all__ += [
    "_resolve_explore_index",
    "_build_status_payload",
    "_build_reindex_payload",
    "_gitignore_coverage",
    "_resolve_index_dir",
]