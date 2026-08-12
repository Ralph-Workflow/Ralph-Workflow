"""Operator-facing workspace health collection for ``ralph workspace-health``.

AC-11: users must see storage use by category, freshness/readiness,
coverage gaps, active observation/refresh/cleanup/recovery work,
watch-capacity status, and cleanup eligibility without inspecting
internal files.

``collect_workspace_health`` is a pure read: it composes the existing
side-effect-free seams (``inventory_storage`` + ``plan_cleanup``,
``WorkspaceAwareness.snapshot()``, and the shared ``WorkspaceMonitor``
lease table) into one JSON-serializable dict. It never mutates the
workspace — the active-cleanup source is the non-mutating
``plan_cleanup`` planner, never the deletion-oriented sweep.
"""

from __future__ import annotations

import time
from pathlib import Path

from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.workspace.awareness import awareness_for_workspace
from ralph.workspace.storage_lifecycle import inventory_storage, plan_cleanup

_READINESS_TOKENS = frozenset(
    {"current", "pending_refresh", "partial", "stale", "unavailable", "live_fallback"}
)


def _derive_readiness(freshness: str) -> str:
    """Map the awareness freshness token to the AC-11 readiness vocabulary."""
    token = "pending_refresh" if freshness == "pending" else freshness
    return token if token in _READINESS_TOKENS else "unavailable"


def _active_observation() -> list[dict[str, object]]:
    """One entry per active ``WorkspaceMonitor`` lease (scope + status)."""
    entries: list[dict[str, object]] = []
    for key, monitors in WorkspaceMonitor.shared_watch_snapshot():
        entries.extend(
            {
                "scope": key,
                "workspace": str(monitor._workspace),
                "started": monitor._started,
                "awareness": monitor.awareness_status,
            }
            for monitor in monitors
        )
    return entries


def collect_workspace_health(workspace_root: Path) -> dict[str, object]:
    """Collect the AC-11 workspace health payload (read-only, JSON-serializable).

    Returns one dict with the keys AC-11 requires: ``storage``,
    ``freshness``, ``readiness``, ``coverage_gaps``,
    ``active_observation``, ``active_refresh``, ``active_cleanup``,
    ``active_recovery``, ``watch_capacity``, ``cleanup_eligibility``,
    and ``recreatability``.
    """
    root = Path(workspace_root).absolute()
    snapshot = awareness_for_workspace(root).snapshot()
    inventory = inventory_storage(root)
    cleanup_plan = plan_cleanup(inventory)

    freshness = str(snapshot.get("freshness", "unavailable"))
    readiness = _derive_readiness(freshness)

    storage_rows: list[dict[str, object]] = []
    cleanup_eligibility: dict[str, str] = {}
    recreatability: dict[str, bool] = {}
    for row in inventory:
        category = str(row["category"])
        row_paths = row["paths"]
        paths = [str(path) for path in row_paths] if isinstance(row_paths, (list, tuple)) else []
        storage_rows.append(
            {
                "category": category,
                "bytes": int(row["bytes"]) if isinstance(row["bytes"], int) else 0,
                "count": int(row["count"]) if isinstance(row["count"], int) else 0,
                "purpose": str(row["purpose"]),
                "growth_trigger": str(row["growth_trigger"]),
                "retention_basis": str(row["retention_basis"]),
                "paths": paths,
            }
        )
        cleanup_eligibility[category] = str(row["eligibility_reason"])
        recreatability[category] = bool(row["recreatable"])

    active_cleanup: dict[str, object] = {
        "candidate_count": len(cleanup_plan),
        "by_category": {},
    }
    by_category: dict[str, int] = {}
    for candidate in cleanup_plan:
        category = str(candidate["category"])
        by_category[category] = by_category.get(category, 0) + 1
    active_cleanup["by_category"] = by_category

    dirty_paths = snapshot.get("dirty_paths_count", 0)
    return {
        "workspace": str(root),
        "generated_at": time.time(),
        "storage": storage_rows,
        "freshness": {
            "token": freshness,
            "cause": snapshot.get("cause"),
            "automatic_recovery": bool(snapshot.get("automatic_recovery")),
        },
        "readiness": readiness,
        "coverage_gaps": {
            "unreadable_paths": [],
            "unsupported_extensions": [],
            "dirty_paths_count": dirty_paths if isinstance(dirty_paths, int) else 0,
        },
        "active_observation": _active_observation(),
        "active_refresh": [],
        "active_cleanup": active_cleanup,
        "active_recovery": [],
        "watch_capacity": {
            "mode": snapshot.get("mode"),
            "cause": snapshot.get("cause"),
            "automatic_recovery": bool(snapshot.get("automatic_recovery")),
            "safe_next_action": snapshot.get("safe_next_action"),
        },
        "cleanup_eligibility": cleanup_eligibility,
        "recreatability": recreatability,
    }


__all__ = ["collect_workspace_health"]
