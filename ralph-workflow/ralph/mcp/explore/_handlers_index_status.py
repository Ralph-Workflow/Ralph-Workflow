"""MCP handler for ``ralph_index_status``.

Extracted from :mod:`ralph.mcp.explore.handlers` so the hub module
stays under the repository's per-file line ceiling. The handler is
the only public surface; the helpers (``_build_disabled_status_payload``,
``_gitignore_repair_payload``, ``_build_status_payload``) are
implementation detail and remain importable from this module for
test reach.

The handler always returns a structured payload: when the session has
no explore index handle it reports ``enabled=False`` /
``index_exists=False`` rather than raising. Side-effect free: must not
create SQLite files, ``.agent/ralph-explore/`` directories, or modify
gitignore state.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.explore import handlers as handlers_module
from ralph.mcp.explore.store import row_str
from ralph.mcp.tools.coordination import (
    CoordinationSessionLike,
    ToolContent,
    ToolResult,
    require_capability,
)
from ralph.mcp.tools.workspace._utils import (
    WORKSPACE_METADATA_READ_CAPABILITY,
    _tool_json,
)

if TYPE_CHECKING:
    from ralph.mcp.explore.handlers import ExploreIndex


def handle_ralph_index_status(
    session: CoordinationSessionLike,
    workspace: object,
    params: dict[str, object],
) -> ToolResult:
    """Report index health and freshness.

    Capability: ``WorkspaceMetadataRead`` (read-only metadata).

    Side-effect free contract: this handler MUST NOT create SQLite
    files, ``.agent/ralph-explore/`` directories, or modify gitignore
    state. When the session has no explore index handle, it inspects
    the existing on-disk state and reports ``enabled=False`` /
    ``index_exists=False`` so callers can decide whether to run
    ``ralph_reindex``.
    """
    require_capability(
        session, WORKSPACE_METADATA_READ_CAPABILITY, "Explore index status"
    )
    workspace_root_obj: object = getattr(workspace, "root", None)
    workspace_root_raw: object = workspace_root_obj or params.get(
        "workspace_root", ""
    )
    workspace_root_str: str = (
        str(workspace_root_raw) if workspace_root_raw else ""
    )
    workspace_root = Path(workspace_root_str) if workspace_root_str else Path.cwd()
    handle: ExploreIndex | None = handlers_module._resolve_explore_index(session)
    if handle is None:
        # Side-effect free: inspect existing disk state without
        # creating files. ``enabled=False`` reports the absence; the
        # caller decides whether to run ``ralph_reindex`` to enable it.
        index_dir = handlers_module._resolve_index_dir(workspace_root)
        db_path = index_dir / "index.sqlite"
        index_exists_on_disk = db_path.is_file()
        cold_index_required = not index_exists_on_disk
        return ToolResult(
            content=[
                ToolContent.text_content(
                    _tool_json(
                        _build_disabled_status_payload(
                            workspace_root,
                            cold_index_required=cold_index_required,
                            index_exists=index_exists_on_disk,
                        )
                    )
                )
            ],
            is_error=False,
        )
    cold_index_required = handle.generation == 0
    payload = _build_status_payload(handle, workspace_root, cold_index_required)
    payload["enabled"] = True
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload))],
        is_error=False,
    )


def _build_disabled_status_payload(
    workspace_root: Path,
    *,
    cold_index_required: bool,
    index_exists: bool = False,
) -> dict[str, object]:
    """Return the side-effect-free status payload when no handle exists.

    ``index_exists`` lets the caller surface an existing on-disk
    persisted index even when the current session has no handle
    attached, so a side-effect-free status check reports the real
    disk state instead of always reporting ``index_exists=False``.

    AC-04: when ``.agent/ralph-explore/`` is not covered by the
    managed gitignore rule, ``managed_ignore_rule_repair`` carries
    the next Ralph seeding repair instruction so callers know how
    to make the cache disposable. The status handler never mutates
    the gitignore on its own \u2014 the repair is a documented next
    step, not a side effect.
    """
    return {
        "enabled": False,
        "index_exists": index_exists,
        "generation": 0,
        "indexed_at": None,
        "files_indexed": 0,
        "files_stale": 0,
        "last_job": None,
        "capabilities": [],
        "graph_backend": "sqlite",
        "dirty_paths_count": 0,
        "cold_index_required": cold_index_required,
        "last_refresh_kind": "none",
        "is_stale": False,
        "stale_paths_count": 0,
        "index_storage_bytes": 0,
        "managed_ignore_rule_present": handlers_module._gitignore_coverage(workspace_root),
        "managed_ignore_rule_repair": _gitignore_repair_payload(workspace_root),
    }


def _gitignore_repair_payload(workspace_root: Path) -> dict[str, object]:
    """Return the structured next-Ralph-seeding repair for the explore ignore rule.

    The repair is a documented next step, not a side effect. The
    handler must NOT mutate the gitignore; it only reports the
    instruction so an operator (or the next ``ralph`` invocation,
    which already calls ``auto_seed_default_gitignore``) can fix the
    coverage.

    AC-05: the repair payload names the explicit ``.agent/ralph-explore/``
    child rule so the next ``auto_seed_default_gitignore`` pass
    appends it next to the parent ``.agent/`` rule. Parent-only
    coverage is reported as ``"parent_only"`` so operators and tests
    see whether the explicit child rule is present yet; ``action``
    becomes ``"append_explicit_child_rule"`` so the next normal
    Ralph seeding pass repairs the gap instead of leaving the
    parent-only state in place.
    """
    gitignore = Path(workspace_root) / ".gitignore"
    rule_present = handlers_module._gitignore_coverage(workspace_root)
    if rule_present:
        # Parent ``.agent/`` coverage plus the explicit child rule
        # is the fully repaired state. Both reports surface the
        # current truth so callers do not need to learn a new
        # status string for the existing-parent-only case.
        if _gitignore_child_rule_present(workspace_root):
            return {
                "required": False,
                "action": "none",
                "reason": "managed_ignore_rule_present",
                "coverage": "explicit_child_rule",
            }
        return {
            "required": True,
            "action": "append_explicit_child_rule",
            "reason": "managed_ignore_rule_parent_only",
            "coverage": "parent_only",
            "missing_rule": ".agent/ralph-explore/",
            "target_file": str(gitignore),
            "next_command": "ralph",
            "description": (
                "Parent ``.agent/`` coverage is in place but the "
                "explicit ``.agent/ralph-explore/`` child rule is "
                "missing. Run a normal `ralph` invocation (or "
                "`auto_seed_default_gitignore`) to append the child "
                "rule so the disposable explore cache is not "
                "committed."
            ),
        }
    return {
        "required": True,
        "action": "seed_default_gitignore",
        "reason": "managed_ignore_rule_missing",
        "target_file": str(gitignore),
        "patterns_to_append": [".agent/", ".agent/ralph-explore/"],
        "next_command": "ralph",
        "description": (
            "Run a normal `ralph` invocation (or "
            "`auto_seed_default_gitignore`) to seed the default "
            ".gitignore so .agent/ralph-explore/ stays a "
            "disposable cache and is not committed. Both the "
            "parent ``.agent/`` rule and the explicit "
            "``.agent/ralph-explore/`` child rule will be appended."
        ),
    }


def _gitignore_child_rule_present(workspace_root: Path) -> bool:
    """Return True when the explicit ``.agent/ralph-explore/`` rule is present."""
    gitignore = Path(workspace_root) / ".gitignore"
    if not gitignore.is_file():
        return False
    try:
        text = gitignore.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(
        line.strip() == ".agent/ralph-explore/" for line in text.splitlines()
    )


def _build_status_payload(
    handle: ExploreIndex,
    workspace_root: Path,
    cold_index_required: bool,
) -> dict[str, object]:
    store = handle.store
    latest_row: sqlite3.Row | None = store.latest_job()
    finished_value: str = (
        row_str(latest_row, "finished_at") if latest_row is not None else ""
    )
    indexed_at: float | None
    if finished_value == "":
        indexed_at = None
    else:
        try:
            indexed_at = float(finished_value)
        except ValueError:
            indexed_at = None
    dirty_paths = store.peek_dirty_paths()
    # Ponytail: bounded aggregates, not ``iter_files()``; both
    # are single ``COUNT(*)`` queries so the status call does
    # not materialize the entire ``files`` table.
    files_indexed = store.count_files()
    # Ponytail: ``files_stale`` counts deleted files; ``is_stale``
    # is true when dirty paths exist OR a deleted file still has
    # a row. ``iter_files`` filters out deleted rows, so the
    # count must come from a dedicated aggregate.
    stale_paths = store.count_deleted_files()
    is_stale = bool(dirty_paths) or stale_paths > 0
    last_job_dict: dict[str, object] | None
    if latest_row is None:
        last_job_dict = None
    else:
        # Ponytail: ``sqlite3.Row`` iterates values, not column names.
        # ``dict(row)`` uses ``row.keys()`` internally and stringifies
        # values, so we do not need a manual comprehension here.
        row_dict: dict[str, object] = dict(latest_row)
        last_job_dict = {str(key): str(value) for key, value in row_dict.items()}
    return {
        "index_exists": handle.generation > 0,
        "generation": handle.generation,
        "indexed_at": indexed_at,
        "files_indexed": files_indexed,
        "files_stale": stale_paths,
        "last_job": last_job_dict,
        "capabilities": ["evidence_lookup", "fts_search"],
        "graph_backend": "sqlite",
        "dirty_paths_count": len(dirty_paths),
        "cold_index_required": cold_index_required,
        "last_refresh_kind": handle.last_refresh_kind,
        "is_stale": is_stale,
        "stale_paths_count": stale_paths,
        "index_storage_bytes": handle.index_storage_bytes(),
        "managed_ignore_rule_present": handlers_module._gitignore_coverage(workspace_root),
        "managed_ignore_rule_repair": _gitignore_repair_payload(workspace_root),
    }
