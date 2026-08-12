"""Side-effect-free inventory and conservative cleanup planning for workspace storage."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.workspace.agent_dir_retention import DEFAULT_MAX_AGE_SECONDS

if TYPE_CHECKING:
    from collections.abc import Callable

_CATEGORY_POLICIES: tuple[
    tuple[str, tuple[str, ...], str, str, str, str, bool, str, str, float | None],
    ...,
] = (
    (
        "project_content",
        (".",),
        "user source and project content",
        "user changes",
        "explicit user retention",
        "project content is never cleanup eligible",
        False,
        "loss is unrecoverable",
        "user",
        None,
    ),
    (
        "workflow_records",
        (
            ".agent/receipts",
            ".agent/state.db",
            ".agent/artifacts/history",
            ".agent/prompt_history",
            ".agent/raw",
        ),
        "workflow receipts and recoverable state",
        "workflow runs",
        "required recovery evidence",
        "required records are never cleanup eligible",
        False,
        "needed to recover workflows",
        "workflow",
        None,
    ),
    (
        "workspace_intelligence",
        (".agent/ralph-explore",),
        "derived workspace intelligence",
        "indexing",
        "rebuildable derived data",
        "derived intelligence can be rebuilt",
        True,
        "rebuilt on next index",
        "explore",
        None,
    ),
    (
        "operational_records",
        (".agent/logs",),
        "operational logs and diagnostics",
        "run diagnostics",
        "required operational evidence",
        "operational records older than the age bound are recreatable",
        True,
        "needed for diagnosis",
        "operations",
        DEFAULT_MAX_AGE_SECONDS,
    ),
    (
        "temporary_data",
        (".agent/tmp",),
        "interrupted-run temporary data",
        "run scratch creation",
        "inactive run ownership",
        "inactive temporary data can be discarded",
        True,
        "interrupted work is restarted",
        "run",
        None,
    ),
)


def _usage(path: Path, *, exclude_agent: bool = False) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    if path.is_file():
        try:
            return path.stat().st_size, 1
        except OSError:
            return 0, 0
    total = count = 0
    try:
        for child in path.rglob("*"):
            if exclude_agent and ".agent" in child.relative_to(path).parts:
                continue
            try:
                if child.is_file():
                    total += child.stat().st_size
                    count += 1
            except OSError:
                continue
    except OSError:
        pass
    return total, count


def inventory_storage(workspace_root: Path) -> tuple[dict[str, object], ...]:
    """Return five category inventories without modifying the workspace.

    Each category row carries ``growth_trigger``, ``retention_basis``,
    ``eligibility_reason``, ``recreatable``, ``recovery_impact``, and
    ``active_owner`` plus a ``paths`` tuple covering every accumulating
    writer the category owns: completion sentinels, receipt directories,
    agent-retry scratch, codex-home directories, MCP session JSON, the
    run state DB, the explore cache directory, log records, history
    directories, and run-tmp directories.
    """
    inventory: list[dict[str, object]] = []
    for (
        category,
        relatives,
        purpose,
        trigger,
        retention,
        eligibility,
        recreatable,
        impact,
        owner,
        max_age_seconds,
    ) in _CATEGORY_POLICIES:
        paths = tuple(workspace_root / relative for relative in relatives)
        bytes_used = 0
        count = 0
        for path in paths:
            path_bytes, path_count = _usage(path, exclude_agent=category == "project_content")
            bytes_used += path_bytes
            count += path_count
        inventory.append(
            {
                "category": category,
                "path": paths[0],
                "paths": paths,
                "purpose": purpose,
                "bytes": bytes_used,
                "count": count,
                "growth_trigger": trigger,
                "retention_basis": retention,
                "eligibility_reason": eligibility,
                "recreatable": recreatable,
                "recovery_impact": impact,
                "active_owner": owner,
                "max_age_seconds": max_age_seconds,
            }
        )
    return tuple(inventory)


def plan_cleanup(
    inventory: tuple[dict[str, object], ...],
    keep_run_ids: frozenset[str] = frozenset(),
    *,
    retained_paths: tuple[Path, ...] = (),
    now: Callable[[], float] | None = None,
) -> tuple[dict[str, object], ...]:
    """Return removable derived/inactive paths without deleting any data.

    ``keep_run_ids`` is the set of run ids whose data must survive the
    plan: temporary directories matching a kept id are skipped, and
    operational log files whose ``<run_id>.log`` prefix matches a kept
    id are retained regardless of age. Callers should derive this set
    from the shared active-run registry via
    ``prune_lock_run_ids(workspace_root, extra_keep={...})`` so runs
    registered by other processes are respected.
    """
    current = (now or time.time)()
    candidates: list[dict[str, object]] = []
    for entry in inventory:
        category = entry["category"]
        path = entry["path"]
        if not isinstance(path, Path):
            continue
        if category == "workspace_intelligence" and path.exists() and path not in retained_paths:
            candidates.append(
                {"category": category, "path": path, "reason": entry["eligibility_reason"]}
            )
        elif category == "temporary_data" and path.is_dir():
            candidates.extend(
                {"category": category, "path": child, "reason": entry["eligibility_reason"]}
                for child in sorted(path.iterdir())
                if child.name not in keep_run_ids and child not in retained_paths
            )
        elif category == "operational_records" and path.is_dir():
            max_age_seconds = entry.get("max_age_seconds")
            if isinstance(max_age_seconds, (int, float)):
                cutoff = current - float(max_age_seconds)
                for child in sorted(path.iterdir()):
                    if not child.is_file() or child in retained_paths:
                        continue
                    run_id = child.name.removesuffix(".log")
                    if run_id in keep_run_ids:
                        continue
                    try:
                        is_aged = child.stat().st_mtime < cutoff
                    except OSError:
                        continue
                    if is_aged:
                        candidates.append(
                            {
                                "category": category,
                                "path": child,
                                "reason": entry["eligibility_reason"],
                            }
                        )
    return tuple(candidates)


__all__ = ["inventory_storage", "plan_cleanup"]
