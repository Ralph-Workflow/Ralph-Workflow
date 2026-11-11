
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
from ralph.workspace.awareness import awareness_for_workspace

if TYPE_CHECKING:
    from ralph.mcp.explore.handlers import ExploreIndex


def handle_ralph_index_status(
    session: CoordinationSessionLike,
    workspace: object,
    params: dict[str, object],
) -> ToolResult:
    require_capability(session, WORKSPACE_METADATA_READ_CAPABILITY, "Explore index status")
    workspace_root_obj: object = getattr(workspace, "root", None)
    workspace_root_raw: object = workspace_root_obj or params.get("workspace_root", "")
    workspace_root_str: str = str(workspace_root_raw) if workspace_root_raw else ""
    workspace_root = Path(workspace_root_str) if workspace_root_str else Path.cwd()
    handle: ExploreIndex | None = handlers_module._resolve_explore_index(session)
    if handle is None:
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
    payload["workspace_awareness"] = awareness_for_workspace(workspace_root).snapshot()
    from ralph.mcp.explore.serving import serving_metadata

    payload.update(serving_metadata(session, index_used=True, fallback_reason=None))
    from ralph.mcp.explore.recovery import build_scheduler

    if cold_index_required:
        from ralph.mcp.explore.recovery import enqueue_recovery

        enqueue_recovery(
            workspace_root,
            "no_committed_generation",
            message="cold_index_required observed by ralph_index_status",
        )
    scheduler = build_scheduler(workspace_root)
    payload["recovery"] = scheduler.snapshot()
    payload["health"] = scheduler.health.value
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
        "workspace_awareness": awareness_for_workspace(workspace_root).snapshot(),
    }


def _gitignore_repair_payload(workspace_root: Path) -> dict[str, object]:
    gitignore = Path(workspace_root) / ".gitignore"
    rule_present = handlers_module._gitignore_coverage(workspace_root)
    if rule_present:
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
    gitignore = Path(workspace_root) / ".gitignore"
    if not gitignore.is_file():
        return False
    try:
        text = gitignore.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(line.strip() == ".agent/ralph-explore/" for line in text.splitlines())


def _build_status_payload(
    handle: ExploreIndex,
    workspace_root: Path,
    cold_index_required: bool,
) -> dict[str, object]:
    store = handle.store
    latest_row: sqlite3.Row | None = store.latest_job()
    finished_value: str = row_str(latest_row, "finished_at") if latest_row is not None else ""
    indexed_at: float | None
    if finished_value == "":
        indexed_at = None
    else:
        try:
            indexed_at = float(finished_value)
        except ValueError:
            indexed_at = None
    dirty_paths = store.peek_dirty_paths()
    files_indexed = store.count_files()
    stale_paths = store.count_deleted_files()
    is_stale = bool(dirty_paths) or stale_paths > 0
    last_job_dict: dict[str, object] | None
    if latest_row is None:
        last_job_dict = None
    else:
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
