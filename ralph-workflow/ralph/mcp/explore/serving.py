
from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from ralph.mcp.explore.handlers import ExploreIndex

if TYPE_CHECKING:
    from ralph.mcp.explore.store import ExploreStore


CANONICAL_REASON_CODES: Final[frozenset[str]] = frozenset(
    {
        "no_committed_generation",
        "version_mismatch",
        "index_corrupt",
        "interrupted_build",
        "index_locked",
        "index_unwritable",
        "index_read_only",
        "index_stale_scope",
        "ignore_rule_changed",
        "hard_file_skipped",
        "pattern_not_fts_eligible",
        "timeout_exceeded",
        "indexer_error",
        "resource_pressure",
        "workspace_moved",
        "no_index_handle",
    }
)

DEFAULT_STALENESS_THRESHOLD: Final[float] = 0.05


def serving_metadata(
    session: object,
    *,
    index_used: bool,
    fallback_reason: str | None,
) -> dict[str, object]:
    if fallback_reason is not None and fallback_reason not in CANONICAL_REASON_CODES:
        raise ValueError(
            f"unknown fallback_reason {fallback_reason!r}; "
            f"must be one of {sorted(CANONICAL_REASON_CODES)}"
        )
    raw_handle: object = getattr(session, "explore_index", None)
    handle: ExploreIndex | None = raw_handle if isinstance(raw_handle, ExploreIndex) else None
    block: dict[str, object] = {
        "index_used": bool(index_used),
        "fallback_reason": fallback_reason,
    }
    block["index_staleness"] = _staleness_block(handle)
    return block


def _staleness_block(handle: ExploreIndex | None) -> dict[str, object]:
    if handle is None:
        return {
            "stale_paths_count": 0,
            "last_refresh_age": None,
            "recovery_willfallback": False,
        }
    store: ExploreStore = handle.store
    try:
        dirty_paths = list(store.peek_dirty_paths())
    except Exception:
        dirty_paths = []
    try:
        deleted_count = int(store.count_deleted_files())
    except Exception:
        deleted_count = 0
    stale_paths_count = len(dirty_paths) + deleted_count
    last_refresh_age: float | None = None
    try:
        latest = store.latest_job()
    except Exception:
        latest = None
    finished_at: float | None = None
    if latest is not None:
        try:
            fin_obj: object = latest["finished_at"]
            if isinstance(fin_obj, (int, float)):
                finished_at = float(fin_obj)
                if finished_at > 0:
                    last_refresh_age = max(0.0, time.time() - finished_at)
        except (KeyError, TypeError, ValueError, IndexError):
            last_refresh_age = None
    raw_cached: object = getattr(handle, "staleness_block_cache", None)
    cached: tuple[tuple[int, int, float | None, int], dict[str, object]] | None
    if raw_cached is None:
        cached = None
    else:
        cached = cast(
            "tuple[tuple[int, int, float | None, int], dict[str, object]]",
            raw_cached,
        )
    signature = (
        stale_paths_count,
        int(finished_at) if finished_at is not None else 0,
        finished_at,
        int(store.get_setting("current_generation") or 0),
    )
    if cached is not None and cached[0] == signature:
        cached_block = dict(cached[1])
        if last_refresh_age is not None:
            cached_block["last_refresh_age"] = last_refresh_age
        with contextlib.suppress(Exception):
            handle.staleness_block_cache = (signature, cached_block)
        return cached_block
    recovery_willfallback = False
    if stale_paths_count > 0:
        try:
            total = int(store.count_files())
        except Exception:
            total = 0
        if total > 0:
            share = stale_paths_count / max(1, total)
            recovery_willfallback = share > DEFAULT_STALENESS_THRESHOLD
    result: dict[str, object] = {
        "stale_paths_count": stale_paths_count,
        "last_refresh_age": last_refresh_age,
        "recovery_willfallback": recovery_willfallback,
    }
    with contextlib.suppress(Exception):
        handle.staleness_block_cache = (signature, result)
    return result


def attach_serving_metadata(
    payload: dict[str, object],
    session: object,
    *,
    index_used: bool,
    fallback_reason: str | None,
) -> dict[str, object]:
    payload.update(
        serving_metadata(session, index_used=index_used, fallback_reason=fallback_reason)
    )
    return payload


def staleness_probe(
    session: object,
    *,
    workspace_root: Path | None = None,
    threshold: float = DEFAULT_STALENESS_THRESHOLD,
) -> dict[str, object]:
    raw_handle: object = getattr(session, "explore_index", None)
    handle: ExploreIndex | None = raw_handle if isinstance(raw_handle, ExploreIndex) else None
    if handle is None or workspace_root is None:
        return {
            "stale": False,
            "reason": None,
            "stale_paths_count": 0,
            "stale_share": 0.0,
            "scope_affected": False,
            "manifest_drift_paths": 0,
        }
    store: ExploreStore = handle.store
    try:
        dirty_paths = list(store.peek_dirty_paths())
    except Exception:
        dirty_paths = []
    try:
        deleted_count = int(store.count_deleted_files())
    except Exception:
        deleted_count = 0
    stale_paths_count = len(dirty_paths) + deleted_count

    manifest_drift_paths = 0
    try:
        committed_raw = store.get_setting("current_generation") or "0"
        try:
            committed_int = int(committed_raw)
        except (TypeError, ValueError):
            committed_int = 0
        if committed_int > 0:
            from ralph.mcp.explore._store_types import collect_workspace_files

            current_manifest = collect_workspace_files(Path(workspace_root))
            current_paths = [rel for rel, _size, _mtime in current_manifest]
            try:
                persisted = store.bulk_size_mtime_for_paths(current_paths)
            except Exception:
                persisted = {}
            for rel, size, mtime_ns in current_manifest:
                row_tuple = persisted.get(rel)
                if row_tuple is None:
                    manifest_drift_paths += 1
                    continue
                row_size, row_mtime = row_tuple
                if row_size != size or row_mtime != mtime_ns:
                    manifest_drift_paths += 1
    except Exception:
        manifest_drift_paths = 0

    if stale_paths_count == 0 and manifest_drift_paths == 0:
        return {
            "stale": False,
            "reason": None,
            "stale_paths_count": 0,
            "stale_share": 0.0,
            "scope_affected": False,
            "manifest_drift_paths": 0,
        }
    try:
        total_files = int(store.count_files())
    except Exception:
        total_files = 0
    effective_stale = stale_paths_count + manifest_drift_paths
    stale_share: float = (effective_stale / max(1, total_files)) if total_files else 1.0
    scope_affected = bool(dirty_paths) or manifest_drift_paths > 0
    stale = scope_affected or (stale_share > threshold)
    probe_result: dict[str, object] = {
        "stale": stale,
        "reason": "index_stale_scope" if stale else None,
        "stale_paths_count": effective_stale,
        "stale_share": stale_share,
        "scope_affected": scope_affected,
        "manifest_drift_paths": manifest_drift_paths,
    }
    return probe_result


__all__ = [
    "CANONICAL_REASON_CODES",
    "DEFAULT_STALENESS_THRESHOLD",
    "attach_serving_metadata",
    "serving_metadata",
    "staleness_probe",
]
