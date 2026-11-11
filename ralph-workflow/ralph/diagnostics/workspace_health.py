
from __future__ import annotations

import time
from pathlib import Path

from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.diagnostics.host_pressure import read_host_pressure
from ralph.workspace.awareness import awareness_for_workspace
from ralph.workspace.storage_lifecycle import inventory_storage, plan_cleanup

_READINESS_TOKENS = frozenset(
    {"current", "pending_refresh", "partial", "stale", "unavailable", "live_fallback"}
)


def _derive_readiness(freshness: str) -> str:
    token = "pending_refresh" if freshness == "pending" else freshness
    return token if token in _READINESS_TOKENS else "unavailable"


def _active_observation() -> list[dict[str, object]]:
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


def _active_maintenance() -> dict[str, object]:
    retention_passes = 0
    try:
        from ralph.workspace.agent_dir_retention import process_retention_coordinator

        retention_passes = process_retention_coordinator().passes
    except Exception:
        retention_passes = 0
    dirty_pending = False
    try:
        from ralph.mcp.explore.dirty_paths import _dirty_scheduler

        dirty_pending = _dirty_scheduler.has_pending
    except Exception:
        dirty_pending = False
    return {
        "retention_passes": retention_passes,
        "dirty_scheduler_pending": dirty_pending,
    }


def collect_workspace_health(workspace_root: Path) -> dict[str, object]:
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
    mode = snapshot.get("mode")
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
            "mode": mode,
            "cause": snapshot.get("cause"),
            "automatic_recovery": bool(snapshot.get("automatic_recovery")),
            "safe_next_action": snapshot.get("safe_next_action"),
        },
        "host_watch_pressure": {
            "ralph_descriptor": {
                "attribution": "certain",
                "mode": mode,
                "owner": "WorkspaceMonitor",
            },
            "host_pressure": read_host_pressure(),
        },
        "active_maintenance": _active_maintenance(),
        "cleanup_eligibility": cleanup_eligibility,
        "recreatability": recreatability,
    }


__all__ = ["collect_workspace_health"]
