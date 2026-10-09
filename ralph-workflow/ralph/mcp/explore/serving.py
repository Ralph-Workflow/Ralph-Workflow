"""Canonical serving metadata for the indexed exploration substrate.

Per ``docs/agents/explore-index-fault-matrix.md`` and the
``PRODUCT_CRITERIA.md`` R1 contract, every index-capable tool
response carries the same three-machine-readable fields:

* ``index_used`` — whether the index served the call (``True`` /
  ``False``).
* ``fallback_reason`` — one of the canonical reason codes from the
  fault matrix, or ``None`` when the index served the call. The
  reason code is stable; new codes must be added to the fault
  matrix first.
* ``index_staleness`` — ``{"stale_paths_count": N, "last_refresh_age": ...}``
  so callers can decide whether to re-query.

The helper is a single source of truth: callers MUST go through
:func:`serving_metadata` rather than recomputing the fields. New
index-capable tools register here so a missing reason code cannot
silently leak into the public surface.
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from ralph.mcp.explore.handlers import ExploreIndex

if TYPE_CHECKING:
    from ralph.mcp.explore.store import ExploreStore


# Canonical reason codes. The fault matrix is authoritative; this
# set MUST stay in sync with that document.
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

#: Default staleness share threshold (5%) above which the freshness
#: guard returns ``index_stale_scope`` and the query falls through.
#: The threshold is documented in
#: ``docs/agents/explore-index-fault-matrix.md`` and is bounded here
#: so the value is immutable across the call sites.
DEFAULT_STALENESS_THRESHOLD: Final[float] = 0.05


def serving_metadata(
    session: object,
    *,
    index_used: bool,
    fallback_reason: str | None,
) -> dict[str, object]:
    """Return the canonical serving metadata block for an index-capable call.

    ``index_used`` must be the truthful source value (``True`` only
    when the index served the call). ``fallback_reason`` must be
    one of :data:`CANONICAL_REASON_CODES` or ``None``. Unknown codes
    raise ``ValueError`` so a typo cannot leak into the public
    surface; the validator runs at the boundary so the error is
    fail-closed.
    """
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
    """Compute the staleness block for the metadata payload.

    ``stale_paths_count`` mirrors the dirty-path queue length and the
    deleted-file count. ``last_refresh_age`` is the wall-clock
    seconds since the index's most recent ``record_job`` write.
    ``recovery_willfallback`` is ``True`` when the staleness
    exceeds the documented default of 5% stale share inside a
    query scope; this is a soft signal for callers, not a
    hard gate (the freshness guard in S-3 enforces the gate).

    The block is cached on the handle keyed by a store-state
    signature ``(dirty_count, deleted_count, finished_at,
    current_generation)``. When the signature is unchanged
    since the previous call (typical for a tight indexed-search
    loop), the cached block is returned without re-querying.
    """
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
    # Re-compute ``last_refresh_age`` against the cached signature
    # so a re-read with the same ``finished_at`` returns the same
    # value the cache was originally bound to (caller's clock may
    # have advanced between calls; the signature uses the raw
    # store value, not the time-relative derived field).
    raw_cached: object = getattr(handle, "staleness_block_cache", None)
    cached: tuple[tuple[int, int, float | None, int], dict[str, object]] | None
    if raw_cached is None:
        cached = None
    else:
        # ``getattr`` returns ``Any`` because the attribute lookup is generic;
        # the class declaration in :mod:`ralph.mcp.explore.handlers` pins the
        # cache type so we can narrow it here.
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
        # Refresh the wall-clock age on hit so a long-lived loop
        # does not report a stale ``last_refresh_age``. Update the
        # cache slot in place so subsequent hits see the same
        # refreshed value.
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
        # Test doubles may not expose the cache slot; the lookup
        # above is forgiving via ``getattr`` so an absent slot is
        # also handled by the next call.
        handle.staleness_block_cache = (signature, result)
    return result


def attach_serving_metadata(
    payload: dict[str, object],
    session: object,
    *,
    index_used: bool,
    fallback_reason: str | None,
) -> dict[str, object]:
    """Mutate ``payload`` in place with the canonical serving metadata.

    Returns the same dict for fluent use. The metadata is appended
    on top of whatever the handler has already produced; the helper
    does NOT clobber caller-owned fields.
    """
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
    """Return the S-3 freshness probe result for a query scope.

    The probe is a cheap bounded check executed before an indexed
    query in ``auto`` mode. It compares the on-disk workspace
    manifest (``collect_workspace_files``) against the persisted
    store rows inside the query scope and returns ``index_stale_scope``
    when ANY stale path falls inside the scope OR the stale share
    exceeds ``threshold``.

    The probe also detects external edits (F12) by comparing the
    workspace ``(size, mtime_ns)`` manifest against the persisted
    ``files`` rows: any drift between current and stored mtime or
    size is reported as ``scope_affected=True`` so the caller falls
    through to live search even when no ``mark_dirty`` call has
    been made. The manifest check is bounded by
    ``workspace_root`` so it cannot blow up on huge repositories.

    Args:
        session: the MCP session (for the explore handle).
        workspace_root: workspace root; when ``None``, the probe returns
            an in-scope verdict of ``False`` (no workspace to check).
        threshold: stale-share threshold (default ``0.05``).

    Returns:
        A dict with ``stale``, ``reason``, ``stale_paths_count``,
        ``stale_share``, ``scope_affected``, and
        ``manifest_drift_paths`` so the caller can decide whether to
        fall through.
    """
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

    # F12 manifest probe: compare current on-disk (size, mtime_ns)
    # against the persisted row's mtime_ns. Any drift = external
    # edit that the dirty-path queue has not seen yet. The probe is
    # bounded by ``workspace_root`` and only consults persisted
    # rows; the comparison is O(n) over the row count which is
    # small relative to the query work it gates. The probe is
    # suppressed when the store has no committed generation so a
    # cold/missing index reports ``no_committed_generation`` rather
    # than the misleading ``index_stale_scope``.
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
                # Probe is best-effort: a closed store / schema drift
                # must not blow up the freshness guard. The
                # dirty-path probe below remains the canonical signal.
                persisted = {}
            for rel, size, mtime_ns in current_manifest:
                row_tuple = persisted.get(rel)
                if row_tuple is None:
                    # New file; not yet in the index.
                    manifest_drift_paths += 1
                    continue
                row_size, row_mtime = row_tuple
                if row_size != size or row_mtime != mtime_ns:
                    manifest_drift_paths += 1
    except Exception:
        # Probe is best-effort: filesystem errors / closed store
        # are non-fatal. The dirty-path probe below is the
        # canonical signal.
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
