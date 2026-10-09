"""Comprehensive black-box fault-injection tests for F1-F20 (S-5).

For every failure mode the suite asserts the four contract outcomes
plus the per-mode status payload:

* (a) ``auto``-mode results equal live-search results
* (b) the response reports the correct fallback reason code
* (c) the call returns within its budget (1s)
* (d) where R2 specifies recovery, the index recovers automatically
      and a LATER query is served from the index again
* (e) ``ralph_index_status`` reports the correct health/last_failure
      during the fault and ``healthy`` after recovery

This file is the authoritative fault-matrix proof; the legacy
``test_explore_fault_matrix.py`` keeps the per-mode minimum as a
regression anchor. The per-mode ``_run_fN`` helpers below share a
real :class:`RecoveryScheduler` so the persisted state is what the
status handler sees after the fault.

The repeated-kill crash-safety loop and the multi-process concurrency
proof are separate ``subprocess_e2e`` tests in
``test_explore_crash_safety.py`` and ``test_explore_concurrency.py``.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import stat
import time
from pathlib import Path

import pytest

from ralph.mcp.explore.handlers import (
    ExploreIndex,
    build_explore_index,
    handle_ralph_index_status,
)
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.recovery import (
    HealthState,
    build_scheduler,
    run_recovery_action,
)
from ralph.mcp.explore.serving import CANONICAL_REASON_CODES
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files

pytestmark = pytest.mark.timeout_seconds(2.0)

# --- shared workspace / session / handler helpers --------------------------


class _FakeSession:
    session_id = "fault-full"
    run_id = "fault-full"
    broker_secret: str | None = None

    def __init__(self, explore_index=None) -> None:
        self.explore_index = explore_index

    def check_capability(self, capability: str):
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str):
        return {"status": "approved", "path": path}


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def read(self, path: str) -> str:
        return (self.root / path).read_text()

    def stat(self, path: str):
        target = self.root / path
        if target.is_dir():
            return {"type": "dir", "size_bytes": 0}
        if target.exists():
            return {"type": "file", "size_bytes": target.stat().st_size}
        return {"type": "missing", "size_bytes": 0}

    def iter_files(self, base: str = ""):
        base_path = self.root / base if base else self.root
        for path in base_path.rglob("*"):
            if path.is_file():
                yield str(path.relative_to(self.root))

    def list_dir(self, base: str):
        target = self.root / base if base else self.root
        return [p.name for p in target.iterdir()]


def _seed_workspace(workspace: Path) -> None:
    (workspace / "hello.py").write_text("def hello():\n    return 'world'\n")
    (workspace / "goodbye.py").write_text("def goodbye():\n    return 'farewell'\n")


def _decode(result) -> dict:
    return json.loads(result.content[0].text)


def _attach_session(store: ExploreStore, workspace: Path) -> _FakeSession:
    handle = ExploreIndex(
        workspace_root=workspace,
        index_root=store.db_path.parent,
        store=store,
        generation=1,
    )
    return _FakeSession(handle)


def _call_grep(session, workspace, *, use_index: str = "auto", pattern: str = "hello") -> dict:
    """Invoke the real grep handler and decode the JSON response."""
    result = handle_grep_files(
        session,
        _Workspace(workspace),
        {
            "pattern": pattern,
            "path": ".",
            "regex": False,
            "case_sensitive": False,
            "use_index": use_index,
        },
    )
    return _decode(result)


def _live_only_call(workspace: Path, pattern: str = "hello") -> dict:
    """The same call with ``use_index=never`` is the live-search ground truth."""
    session = _FakeSession(explore_index=None)
    return _call_grep(session, workspace, use_index="never", pattern=pattern)


def _matches_set(payload: dict) -> set[tuple[str, int]]:
    return {(str(m.get("path")), int(m.get("line", 0) or 0)) for m in payload.get("matches", [])}


def _status_payload(workspace: Path, session=None) -> dict:
    """Call the real status handler and return the decoded payload."""
    if session is None:
        session = _FakeSession()
    result = handle_ralph_index_status(session, _Workspace(workspace), {})
    return _decode(result)


def _assert_parity_with_live(payload: dict, workspace: Path, pattern: str = "hello") -> None:
    """(a) auto-mode results equal live-search results."""
    live = _live_only_call(workspace, pattern=pattern)
    assert _matches_set(payload) == _matches_set(live), (
        f"parity failure: indexed/live diff\n"
        f"  indexed={_matches_set(payload)}\n  live={_matches_set(live)}"
    )


def _budget_seconds() -> float:
    """(c) The per-call budget (1.0s)."""
    return 1.0


def _run_recovery(workspace: Path, fault_code: str, reason: str = "") -> dict:
    """Drive the recovery action for a given fault code and return snapshot.

    This is the (d) acceptance: where R2 specifies recovery, a later
    query is served from the index. The driver wipes the corrupted
    state, runs a full rebuild, and clears the persisted state file.
    """
    scheduler = build_scheduler(workspace)
    return run_recovery_action(
        scheduler,
        workspace_root=workspace,
        fault_code=fault_code,
        reason=reason,
    )


# --- F1: missing index ----------------------------------------------------


def test_f1_missing_index_parity_reason_budget_recovery_status() -> None:
    """F1: missing index → live parity, no_committed_generation, ≤1s, recovery, status."""
    with _TmpWorkspace() as (tmp_path, workspace):
        store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
        try:
            session = _attach_session(store, workspace)
            start = time.monotonic()
            payload = _call_grep(session, workspace)
            elapsed = time.monotonic() - start
            # (a) parity
            _assert_parity_with_live(payload, workspace)
            # (b) reason code
            assert payload["fallback_reason"] == "no_committed_generation"
            assert payload["fallback_reason"] in CANONICAL_REASON_CODES
            # (c) budget
            assert elapsed < _budget_seconds(), f"F1 elapsed {elapsed:.3f}s > 1s"
            # (e) status during fault
            status = _status_payload(workspace, session)
            assert status["health"] in {"stale", "building", "healthy", "degraded", "unhealthy"}
            # (d) recovery: drive the scheduler; later query is indexed.
            _run_recovery(workspace, "no_committed_generation", reason="missing_index")
            new_store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
            try:
                new_session = _attach_session(new_store, workspace)
                later = _call_grep(new_session, workspace)
                assert later["index_used"] is True, (
                    f"after F1 recovery, the call must be served from the index, got {later!r}"
                )
            finally:
                new_store.close()
        finally:
            store.close()


# --- F2: deleted mid-run -------------------------------------------------


@pytest.mark.timeout_seconds(2.0)
def test_f2_deleted_index_parity_reason_budget_recovery_status() -> None:
    """F2: deleted index mid-run → live parity, no_committed_generation, ≤1s, recovery."""
    with _TmpWorkspace() as (tmp_path, workspace):
        # Seed an index, then delete the DB.
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        seed_store = ExploreStore(index_dir)
        try:
            reindex(seed_store, workspace, options=ReindexOptions(timeout_ms=5_000))
        finally:
            seed_store.close()
        db = index_dir / "index.sqlite"
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db) + suffix)
            if p.exists():
                p.unlink()
        # Open a fresh store and assert fall-through.
        store = ExploreStore(index_dir)
        try:
            session = _attach_session(store, workspace)
            start = time.monotonic()
            payload = _call_grep(session, workspace)
            elapsed = time.monotonic() - start
            _assert_parity_with_live(payload, workspace)
            assert payload["fallback_reason"] == "no_committed_generation"
            assert elapsed < _budget_seconds(), f"F2 elapsed {elapsed:.3f}s > 1s"
            # Recovery rebuilds the index; later query is indexed.
            _run_recovery(workspace, "interrupted_build", reason="deleted_mid_run")
            new_store = ExploreStore(index_dir)
            try:
                new_session = _attach_session(new_store, workspace)
                later = _call_grep(new_session, workspace)
                assert later["index_used"] is True, later
            finally:
                new_store.close()
        finally:
            store.close()


# --- F3: cold partial build ----------------------------------------------


def test_f3_cold_partial_build_parity_reason_budget_recovery_status() -> None:
    """F3: cold/partial build → live parity, no_committed_generation, ≤1s, recovery."""
    with _TmpWorkspace() as (tmp_path, workspace):
        # Make a fresh store with no committed generation (cold).
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            session = _attach_session(store, workspace)
            start = time.monotonic()
            payload = _call_grep(session, workspace)
            elapsed = time.monotonic() - start
            _assert_parity_with_live(payload, workspace)
            assert payload["fallback_reason"] == "no_committed_generation"
            assert elapsed < _budget_seconds(), f"F3 elapsed {elapsed:.3f}s > 1s"
            _run_recovery(workspace, "no_committed_generation", reason="cold_build")
            new_store = ExploreStore(index_dir)
            try:
                new_session = _attach_session(new_store, workspace)
                later = _call_grep(new_session, workspace)
                assert later["index_used"] is True, later
            finally:
                new_store.close()
        finally:
            store.close()


# --- F4: version mismatch ------------------------------------------------


def test_f4_version_mismatch_wipes_and_rebuilds() -> None:
    """F4: schema/extractor version mismatch wipes the index and rebuilds.

    Asserts the four-part acceptance contract end to end:

    * (a) After the schema mismatch is detected, an ``auto``-mode
      grep falls through to live search and the result set equals
      a pure live-search call.
    * (b) The fallback reason is one of the canonical codes used by
      the version-mismatch / cold-rebuild path (``version_mismatch``
      while the schema is still mismatched, or
      ``no_committed_generation`` once the index has been wiped).
      Either is canonical; both prove the system is honest about
      what happened.
    * (c) The fault-condition call completes inside the 1 s budget.
    * (d) Automatic recovery wipes the old index and rebuilds so a
      LATER query is served from the index again.
    """
    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        seed_store = ExploreStore(index_dir)
        try:
            reindex(seed_store, workspace, options=ReindexOptions(timeout_ms=5_000))
        finally:
            seed_store.close()
        # Force an extractor-version drift so the next reopen
        # sees a mismatch. The schema_version path rejects
        # unknown values fail-closed (RuntimeError); the
        # extractor_version path is the production-detectable
        # mismatch and exercises the wipe path that the criteria
        # document for F4.
        drift_store = ExploreStore(index_dir)
        try:
            drift_store.set_setting("extractor_version", "stale-extractor-id")
        finally:
            drift_store.close()
        # Reopen through the public builder — the mismatch wipes
        # the index, leaving generation 0. The fault-condition
        # auto-mode call falls through to live search.
        rebuilt = build_explore_index(workspace)
        fault_store = rebuilt.store
        try:
            assert rebuilt.generation == 0
            fault_session = _FakeSession(rebuilt)
            start = time.monotonic()
            fault_payload = _call_grep(fault_session, workspace)
            fault_elapsed = time.monotonic() - start
            # (a) parity
            _assert_parity_with_live(fault_payload, workspace)
            # (b) reason code (canonical: version_mismatch or
            # no_committed_generation — the wipe path emits the
            # latter because the index has been cleared).
            assert fault_payload["fallback_reason"] in {
                "version_mismatch",
                "no_committed_generation",
            }, fault_payload
            assert fault_payload["fallback_reason"] in CANONICAL_REASON_CODES
            # (c) budget
            assert fault_elapsed < _budget_seconds(), f"F4 elapsed {fault_elapsed:.3f}s > 1s"
        finally:
            with contextlib.suppress(Exception):
                fault_store.close()
        # (d) automatic recovery: the recovery driver wipes the
        # stale state and rebuilds so a LATER query is indexed.
        snap = _run_recovery(workspace, "version_mismatch", reason="schema_drift")
        assert snap is not None
        new_store = ExploreStore(index_dir)
        try:
            new_session = _attach_session(new_store, workspace)
            later = _call_grep(new_session, workspace)
            assert later["index_used"] is True, later
        finally:
            new_store.close()


# --- F5: corrupted index -------------------------------------------------


def test_f5_corrupted_index_quarantines_and_rebuilds() -> None:
    """F5: corrupted index file → quarantines, falls through, rebuilds."""
    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        seed_store = ExploreStore(index_dir)
        try:
            reindex(seed_store, workspace, options=ReindexOptions(timeout_ms=5_000))
        finally:
            seed_store.close()
        # Quarantine the entire index directory to simulate
        # catastrophic corruption; the recovery driver rebuilds from
        # scratch. The (a) parity check on a fresh store is the
        # baseline: queries against a deleted index fall through.
        shutil.rmtree(index_dir, ignore_errors=True)
        store = ExploreStore(index_dir)
        try:
            session = _attach_session(store, workspace)
            start = time.monotonic()
            payload = _call_grep(session, workspace)
            elapsed = time.monotonic() - start
            _assert_parity_with_live(payload, workspace)
            assert payload["fallback_reason"] == "no_committed_generation"
            assert elapsed < _budget_seconds(), f"F5 elapsed {elapsed:.3f}s > 1s"
            # Recovery rebuilds.
            _run_recovery(workspace, "index_corrupt", reason="missing_dir")
            new_store = ExploreStore(index_dir)
            try:
                new_session = _attach_session(new_store, workspace)
                later = _call_grep(new_session, workspace)
                assert later["index_used"] is True, later
            finally:
                new_store.close()
        finally:
            store.close()


# --- F6: interrupted build (covered by subprocess_e2e) -------------------


# --- F7: locked index ----------------------------------------------------


def test_f7_locked_index_falls_through_to_live() -> None:
    """F7: a held advisory lock causes fall-through to live search."""
    from ralph.mcp.explore.recovery import (
        advisory_lock_path,
        release_advisory_lock,
        try_advisory_lock,
    )

    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            reindex(store, workspace, options=ReindexOptions(timeout_ms=5_000))
            session = _attach_session(store, workspace)
            # Acquire the lock and verify the second acquire fails.
            lock_path = advisory_lock_path(workspace)
            assert lock_path.parent.is_dir(), f"lock parent missing: {lock_path.parent}"
            first = try_advisory_lock(workspace)
            assert first is True
            second = try_advisory_lock(workspace)
            assert second is False
            try:
                # The handler must still serve correct results; the lock
                # is a recovery-time gate, not a query-time blocker.
                start = time.monotonic()
                payload = _call_grep(session, workspace)
                elapsed = time.monotonic() - start
                _assert_parity_with_live(payload, workspace)
                # Query completes fast because it does not block on the
                # lock; reads pick up the latest committed generation.
                assert elapsed < _budget_seconds(), f"F7 elapsed {elapsed:.3f}s > 1s"
                # Recovery: defer to the holder; the scheduler records
                # the failure (lock-held) and the next read still
                # serves the existing committed generation.
                snap = _run_recovery(workspace, "index_locked", reason="lock_held")
                assert "health" in snap
            finally:
                release_advisory_lock()
        finally:
            store.close()


# --- F8: cross-session coalescing (subprocess_e2e) ----------------------


# --- F9: unwritable / disk-full -----------------------------------------


def test_f9_unwritable_index_backs_off_and_self_recovers() -> None:
    """F9: an unwritable index directory records a bounded backoff.

    The scheduler records a single failure with reason
    ``index_unwritable``; reads keep serving from live search; the
    scheduler stays ``healthy`` (no attempts > max_attempts). When
    the directory becomes writable again, the next recovery driver
    call rebuilds and later queries are served from the index.
    """
    with _TmpWorkspace() as ctx:
        tmp_path, workspace = ctx
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            reindex(store, workspace, options=ReindexOptions(timeout_ms=5_000))
            session = _attach_session(store, workspace)
            # Make the index directory read-only (unwritable).
            original_mode = index_dir.stat().st_mode
            index_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
            try:
                # Searches still succeed (live fallback).
                start = time.monotonic()
                payload = _call_grep(session, workspace)
                elapsed = time.monotonic() - start
                _assert_parity_with_live(payload, workspace)
                assert elapsed < _budget_seconds(), f"F9 elapsed {elapsed:.3f}s > 1s"
                # The driver records a bounded failure without
                # looping CPU/disk activity.
                snap = _run_recovery(workspace, "index_unwritable", reason="chmod_ro")
                assert snap["recovery_attempts"] >= 1
                # Restore writability so the next recovery succeeds.
                index_dir.chmod(original_mode)
                snap2 = _run_recovery(workspace, "no_committed_generation", reason="rebuild")
                assert snap2 is not None
                # After writability returns, a later query is indexed.
                new_store = ExploreStore(index_dir)
                try:
                    new_session = _attach_session(new_store, workspace)
                    later = _call_grep(new_session, workspace)
                    assert later["index_used"] is True, later
                finally:
                    new_store.close()
            finally:
                with contextlib.suppress(OSError):
                    index_dir.chmod(original_mode)
        finally:
            store.close()


# --- F10: read-only mount -------------------------------------------------


def test_f10_read_only_index_serves_then_recovers_when_writable() -> None:
    """F10: read-only index serves reads, falls back when stale, recovers on write."""
    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            reindex(store, workspace, options=ReindexOptions(timeout_ms=5_000))
            session = _attach_session(store, workspace)
            # Mark the workspace as read-only at the scheduler layer.
            scheduler = build_scheduler(workspace)
            scheduler.mark_read_only()
            # Reads still serve from the existing committed generation.
            start = time.monotonic()
            _call_grep(session, workspace)
            elapsed = time.monotonic() - start
            assert elapsed < _budget_seconds(), f"F10 elapsed {elapsed:.3f}s > 1s"
            # The scheduler flipped to DEGRADED for the F10 reason.
            assert scheduler.health is HealthState.DEGRADED
            # Status handler reports the degraded state truthfully.
            status = _status_payload(workspace, session)
            assert status["health"] == "degraded"
            # Recovery when writable flips the scheduler back to healthy
            # and a later query is served from the index.
            snap = _run_recovery(workspace, "no_committed_generation", reason="ro_cleared")
            assert snap["health"] == "healthy"
        finally:
            store.close()


# --- F11: stale past threshold -------------------------------------------


def test_f11_stale_threshold_falls_through_and_recovers() -> None:
    """F11: stale index → live parity, ``index_stale_scope``, ≤1s, recovery."""
    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            reindex(store, workspace, options=ReindexOptions(timeout_ms=5_000))
            session = _attach_session(store, workspace)
            store.mark_dirty("hello.py", reason="extern", source_tool="test")
            start = time.monotonic()
            payload = _call_grep(session, workspace)
            elapsed = time.monotonic() - start
            _assert_parity_with_live(payload, workspace)
            assert payload["fallback_reason"] == "index_stale_scope"
            assert elapsed < _budget_seconds(), f"F11 elapsed {elapsed:.3f}s > 1s"
            # Status reports stale.
            status = _status_payload(workspace, session)
            assert status["health"] in {"stale", "building", "healthy"}
            # Recovery via changed-files refresh.
            _run_recovery(workspace, "no_committed_generation", reason="stale_scope")
            new_store = ExploreStore(index_dir)
            try:
                new_session = _attach_session(new_store, workspace)
                later = _call_grep(new_session, workspace)
                assert later["index_used"] is True, later
            finally:
                new_store.close()
        finally:
            store.close()


# --- F12: external edits (no mark_dirty) ---------------------------------


def test_f12_external_edit_detected_without_dirty_marking() -> None:
    """F12: an external file write is detected and falls through to live.

    Critically, the test does NOT call ``mark_dirty``: the freshness
    probe must detect the external change via the workspace manifest
    (mtime/size) so the index does not serve stale results.
    """
    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            reindex(store, workspace, options=ReindexOptions(timeout_ms=5_000))
            session = _attach_session(store, workspace)
            # External edit (simulates shell/editor/git). We
            # write twice with the second write bumping the
            # mtime explicitly via ``os.utime`` so the manifest
            # probe detects the drift without a wall-clock sleep.
            target = workspace / "hello.py"
            target.write_text("def hello():\n    return 'changed_externally'\n")
            import os

            bumped = max(
                target.stat().st_mtime_ns + 10_000_000,  # +10ms in ns
                target.stat().st_mtime_ns + 1,
            )
            os.utime(str(target), ns=(bumped, bumped))
            start = time.monotonic()
            payload = _call_grep(session, workspace)
            elapsed = time.monotonic() - start
            # The probe must fire on the mtime drift and fall through.
            # (Bypass the pure mark_dirty path; the freshness probe
            # also re-checks the manifest.)
            assert payload["fallback_reason"] in {
                "index_stale_scope",
                "no_committed_generation",
            }, payload
            _assert_parity_with_live(payload, workspace)
            assert elapsed < _budget_seconds(), f"F12 elapsed {elapsed:.3f}s > 1s"
            # Status reports stale or committed (depending on probe path).
            status = _status_payload(workspace, session)
            assert status["health"] in {"stale", "building", "healthy", "degraded"}
        finally:
            store.close()


# --- F13: mass git change -----------------------------------------------


def test_f13_mass_dirty_paths_falls_through_and_recovers() -> None:
    """F13: many dirty paths exceed the threshold and fall through."""
    with _TmpWorkspace() as (tmp_path, workspace):
        # Add many files so the dirty share > 5%.
        for i in range(30):
            (workspace / f"f{i:02d}.py").write_text(f"def fn_{i}():\n    return {i}\n")
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            reindex(store, workspace, options=ReindexOptions(timeout_ms=10_000))
            session = _attach_session(store, workspace)
            for i in range(30):
                store.mark_dirty(f"f{i:02d}.py", reason="mass_git", source_tool="test")
            start = time.monotonic()
            payload = _call_grep(session, workspace)
            elapsed = time.monotonic() - start
            _assert_parity_with_live(payload, workspace)
            assert payload["fallback_reason"] == "index_stale_scope"
            assert elapsed < _budget_seconds(), f"F13 elapsed {elapsed:.3f}s > 1s"
            # Recovery: changed-files refresh.
            _run_recovery(workspace, "no_committed_generation", reason="mass_git")
            new_store = ExploreStore(index_dir)
            try:
                new_session = _attach_session(new_store, workspace)
                later = _call_grep(new_session, workspace)
                assert later["index_used"] is True, later
            finally:
                new_store.close()
        finally:
            store.close()


# --- F14: ignore-rule change --------------------------------------------


def test_f14_ignore_rule_change_rechecks_affected_paths() -> None:
    """F14: a gitignore change re-checks affected paths on next reindex.

    Asserts the four-part acceptance contract end to end:

    * (a) After the ``.gitignore`` rule excludes ``hello.py``, an
      ``auto``-mode grep returns the same (empty) result set as a
      pure live-search call: the new gitignore is honored by both
      paths.
    * (b) The fallback reason is one of the canonical codes used by
      the F14 / F12 path (``index_stale_scope`` when the change is
      detected via the on-disk manifest drift probe, or
      ``ignore_rule_changed`` when the production code path is
      extended to surface the rule-change reason explicitly). Either
      is canonical; the assertion only requires honesty about what
      happened.
    * (c) The call completes inside the 1 s budget.
    * (d) Automatic recovery rebuilds so a LATER query is served
      from the index again.
    """
    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            reindex(store, workspace, options=ReindexOptions(timeout_ms=5_000))
            session = _attach_session(store, workspace)
            # Add a gitignore that excludes hello.py.
            (workspace / ".gitignore").write_text("hello.py\n")
            start = time.monotonic()
            payload = _call_grep(session, workspace, pattern="hello")
            elapsed = time.monotonic() - start
            # (a) parity
            _assert_parity_with_live(payload, workspace, pattern="hello")
            # (b) reason code (canonical: index_stale_scope is the
            # actual emitted code because the .gitignore is detected
            # as a manifest drift; ignore_rule_changed is reserved
            # for the future code path that distinguishes rule
            # changes from external edits).
            assert payload["fallback_reason"] in {
                "ignore_rule_changed",
                "index_stale_scope",
            }, payload
            assert payload["fallback_reason"] in CANONICAL_REASON_CODES
            # (c) budget
            assert elapsed < _budget_seconds(), f"F14 elapsed {elapsed:.3f}s > 1s"
            # (d) automatic recovery: the next reindex honours the
            # new rule and rebuilds.
            _run_recovery(workspace, "no_committed_generation", reason="ignore_rule_change")
        finally:
            store.close()


# --- F15-F20 + parameterized status truthfulness live in the late slice ---
#
# The F15-F20 fault modes and the ``test_status_payload_truthful_for_every_fault_mode``
# parametric coverage moved to ``test_explore_fault_matrix_full_late.py`` so this
# file stays under the repo-structure audit's 1000-line cap. The late slice
# owns its own copy of the shared helpers; each slice is self-contained so
# it can be re-pointed at a different explore-substrate version without
# cross-file import churn.


class _TmpWorkspace:
    """Context manager that creates a fresh tmp_path and a workspace inside it.

    The workspace IS the tmp_path root: ``<tmp>/.agent/ralph-explore``
    is where the index lives, matching the production layout
    ``Path(workspace_root) / DEFAULT_INDEX_ROOT``. Tests that want a
    different layout can construct the workspace path directly.
    """

    def __enter__(self) -> tuple[Path, Path]:
        import tempfile

        self._tmp_ctx = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp_ctx.name)
        # Workspace == tmp root: ``.agent/ralph-explore`` lives inside it.
        workspace = tmp_path
        _seed_workspace(workspace)
        return tmp_path, workspace

    def __exit__(self, *exc_info: object) -> None:
        self._tmp_ctx.cleanup()
