"""Tests for the run-start .agent retention sweep."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from ralph.mcp.artifacts.state_db import MISSING, RunStateDB
from ralph.workspace.agent_dir_retention import RetentionPassCoordinator, sweep_agent_dir

_WEEK = 7 * 24 * 3600.0


def _make_aged(path: Path, age_seconds: float, now: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    stamp = now - age_seconds
    os.utime(path, (stamp, stamp))
    # Walk up the parent chain so the test dir itself is aged.
    cursor = path.parent
    while cursor.parent != cursor:
        try:
            os.utime(cursor, (stamp, stamp))
        except OSError:
            break
        cursor = cursor.parent


def test_removes_old_completion_sentinels_keeps_current(tmp_path: Path) -> None:
    now = 1_000_000_000.0
    agent = tmp_path / ".agent"
    _make_aged(agent / "completion_seen_old.json", _WEEK + 10, now)
    _make_aged(agent / "completion_seen_current.json", _WEEK + 10, now)
    _make_aged(agent / "completion_seen_fresh.json", 60.0, now)

    removed = sweep_agent_dir(tmp_path, keep_run_id="current", now=lambda: now)

    assert not (agent / "completion_seen_old.json").exists()
    assert (agent / "completion_seen_current.json").exists()  # current run kept
    assert (agent / "completion_seen_fresh.json").exists()  # too young
    assert removed == 1


def test_removes_old_receipt_dirs(tmp_path: Path) -> None:
    now = 1_000_000_000.0
    _make_aged(tmp_path / ".agent" / "receipts" / "old-run" / "plan.json", _WEEK + 10, now)
    _make_aged(tmp_path / ".agent" / "receipts" / "current" / "plan.json", _WEEK + 10, now)

    sweep_agent_dir(tmp_path, keep_run_id="current", now=lambda: now)

    assert not (tmp_path / ".agent" / "receipts" / "old-run").exists()
    assert (tmp_path / ".agent" / "receipts" / "current" / "plan.json").exists()


def test_removes_old_agent_retry_scratch(tmp_path: Path) -> None:
    now = 1_000_000_000.0
    _make_aged(tmp_path / ".agent" / "tmp" / "agent_retry_abc.md", _WEEK + 10, now)
    _make_aged(tmp_path / ".agent" / "tmp" / "agent_retry_context_abc.md", _WEEK + 10, now)
    _make_aged(tmp_path / ".agent" / "tmp" / "development_prompt.md", _WEEK + 10, now)

    sweep_agent_dir(tmp_path, keep_run_id=None, now=lambda: now)

    assert not (tmp_path / ".agent" / "tmp" / "agent_retry_abc.md").exists()
    assert not (tmp_path / ".agent" / "tmp" / "agent_retry_context_abc.md").exists()
    # non-matching files untouched
    assert (tmp_path / ".agent" / "tmp" / "development_prompt.md").exists()


def test_sweep_regression_reclaims_only_aged_codex_home_dirs(tmp_path: Path) -> None:
    """S-2: workspace Codex homes are reclaimed only after the age gate."""
    now = 1_000_000_000.0
    tmp_dir = tmp_path / ".agent" / "tmp"
    aged_home = tmp_dir / "codex-home-aged"
    fresh_home = tmp_dir / "codex-home-fresh"
    other_dir = tmp_dir / "other-dir"
    _make_aged(aged_home / "config.toml", _WEEK + 10, now)
    _make_aged(fresh_home / "config.toml", 60.0, now)
    _make_aged(other_dir / "config.toml", _WEEK + 10, now)

    removed = sweep_agent_dir(tmp_path, keep_run_id=None, now=lambda: now)

    assert not aged_home.exists()
    assert fresh_home.exists()
    assert other_dir.exists()
    assert removed == 1


def test_sweep_regression_reclaims_aged_codex_home_with_fresh_symlink_target(
    tmp_path: Path,
) -> None:
    """S-2: Codex-home age checks use the directory metadata, not symlink targets."""
    now = 1_000_000_000.0
    target = tmp_path / "live-codex-file"
    target.write_text("fresh", encoding="utf-8")
    home = tmp_path / ".agent" / "tmp" / "codex-home-symlinked"
    home.mkdir(parents=True)
    (home / "live-file").symlink_to(target)
    stamp = now - _WEEK - 10
    os.utime(home, (stamp, stamp), follow_symlinks=False)

    removed = sweep_agent_dir(tmp_path, keep_run_id=None, now=lambda: now)

    assert not home.exists()
    assert target.exists()
    assert removed == 1


def test_sweep_regression_reclaims_only_aged_mcp_session_files(tmp_path: Path) -> None:
    """S-2: stale MCP session metadata is reclaimed without touching other JSON."""
    now = 1_000_000_000.0
    agent_dir = tmp_path / ".agent"
    aged = agent_dir / "ralph-mcp-session-aged.json"
    fresh = agent_dir / "ralph-mcp-session-fresh.json"
    other = agent_dir / "other.json"
    _make_aged(aged, _WEEK + 10, now)
    _make_aged(fresh, 60.0, now)
    _make_aged(other, _WEEK + 10, now)

    removed = sweep_agent_dir(tmp_path, keep_run_id=None, now=lambda: now)

    assert not aged.exists()
    assert fresh.exists()
    assert other.exists()
    assert removed == 1


def test_missing_agent_dir_is_noop(tmp_path: Path) -> None:
    assert sweep_agent_dir(tmp_path, keep_run_id=None) == 0


def test_sweep_also_prunes_aged_db_rows(tmp_path: Path) -> None:
    """RFC-013 P3: the sweep also prunes aged rows in ``.agent/state.db``
    so DB rows do not accumulate alongside file bookkeeping."""
    db = RunStateDB(tmp_path)
    db.upsert_receipt("old-run", "plan", "sig")
    db.upsert_completion_sentinel("old-run", "sig")
    db.close()

    # Move the clock forward so the inserted rows look aged relative
    # to the DB's real-time ``unixepoch('subsec')`` stamps.
    future_now = time.time() + _WEEK * 2 + 60

    sweep_agent_dir(tmp_path, keep_run_id=None, now=lambda: future_now)

    db2 = RunStateDB(tmp_path)
    assert db2.get_receipt_hmac("old-run", "plan") is MISSING
    assert db2.get_completion_sentinel_hmac("old-run") is MISSING
    db2.close()


def test_db_sweep_preserves_current_run_rows(tmp_path: Path) -> None:
    """RFC-013 P3 + keep_run_id contract: aged DB rows for the current
    run are preserved so the in-flight run does not lose its own
    completion sentinels and receipts at run-start."""
    db = RunStateDB(tmp_path)
    db.upsert_receipt("current", "plan", "sig-current-plan")
    db.upsert_receipt("current", "commit_message", "sig-current-commit")
    db.upsert_completion_sentinel("current", "sig-current-sentinel")
    db.upsert_receipt("old-run", "plan", "sig-old-plan")
    db.upsert_completion_sentinel("old-run", "sig-old-sentinel")
    db.close()

    future_now = time.time() + _WEEK * 2 + 60

    sweep_agent_dir(tmp_path, keep_run_id="current", now=lambda: future_now)

    db2 = RunStateDB(tmp_path)
    # current-run rows are preserved despite their age
    assert db2.get_receipt_hmac("current", "plan") == "sig-current-plan"
    assert db2.get_receipt_hmac("current", "commit_message") == ("sig-current-commit")
    assert db2.get_completion_sentinel_hmac("current") == "sig-current-sentinel"
    # other-run rows are still pruned
    assert db2.get_receipt_hmac("old-run", "plan") is MISSING
    assert db2.get_completion_sentinel_hmac("old-run") is MISSING
    db2.close()


def test_runstate_db_prune_preserves_keep_run_id(tmp_path: Path) -> None:
    """RunStateDB.prune_older_than honours ``keep_run_id`` keyword — keeps
    the in-flight run's rows regardless of age even when invoked
    directly (no sweep_agent_dir wrapper)."""
    db = RunStateDB(tmp_path)
    db.upsert_receipt("current", "plan", "sig-c")
    db.upsert_receipt("old", "plan", "sig-o")
    db.close()

    # Both rows are aged (cutoff is large), but ``current`` is kept.
    cutoff = time.time() + 1000.0
    db2 = RunStateDB(tmp_path)
    try:
        removed = db2.prune_older_than(cutoff, keep_run_id="current")
    finally:
        db2.close()

    assert removed == 1  # only the old-row delete; current-run row skipped
    db3 = RunStateDB(tmp_path)
    try:
        assert db3.get_receipt_hmac("current", "plan") == "sig-c"
        assert db3.get_receipt_hmac("old", "plan") is MISSING
    finally:
        db3.close()


def test_sweep_does_not_create_state_db_when_absent(tmp_path: Path) -> None:
    """sweep_agent_dir must NOT create ``.agent/state.db`` as a side effect.

    RFC-013 P3: ``RunStateDB.__init__`` creates the database on open.
    The retention sweep is intended to reduce filesystem churn, not
    introduce new state files. The DB prune must short-circuit when
    ``state.db`` does not already exist so workspace bootstrapping on
    a fresh repo does not suddenly gain a sqlite WAL pair.
    """
    agent = tmp_path / ".agent"
    agent.mkdir(parents=True, exist_ok=True)

    assert not (tmp_path / ".agent" / "state.db").exists()

    fixed_now = time.time()
    sweep_agent_dir(tmp_path, keep_run_id=None, now=lambda: fixed_now)

    assert not (tmp_path / ".agent" / "state.db").exists()
    assert not (tmp_path / ".agent" / "state.db-wal").exists()
    assert not (tmp_path / ".agent" / "state.db-shm").exists()


def test_retention_coordinator_is_process_local(tmp_path: Path) -> None:
    """S-4: one coordinator coalesces threads, while another owns its own pass."""
    coordinator = RetentionPassCoordinator(on_wave_acquired=threading.Barrier(4).wait)
    results: list[int] = []
    results_lock = threading.Lock()

    def _sweep() -> None:
        removed = sweep_agent_dir(tmp_path, keep_run_id=None, coordinator=coordinator)
        with results_lock:
            results.append(removed)

    threads = [threading.Thread(target=_sweep) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1.0)

    assert len(results) == 4
    # Cross-process coordination is deliberately out of scope.
    assert coordinator.passes == 1
    other = RetentionPassCoordinator()
    assert sweep_agent_dir(tmp_path, keep_run_id=None, coordinator=other) == 0
    assert other.passes == 1


def test_registered_active_runs_survive_parallel_sweeps(tmp_path: Path) -> None:
    """AC-9 (in-process): the active-run registry protects both registered
    runs from every parallel sweep, even when each caller passes a single
    overlapping ``keep_run_id``.

    B6 (independent-process coordination) remains an open gap; this test
    closes only the in-process boundary.
    """
    import threading

    from ralph.workspace.agent_dir_retention import (
        register_active_run,
        unregister_active_run,
    )

    now = 1_000_000_000.0
    # Both runs carry aged bookkeeping that a naive sweep would reclaim.
    _make_aged(tmp_path / ".agent" / "completion_seen_run-a.json", _WEEK + 10, now)
    _make_aged(tmp_path / ".agent" / "completion_seen_run-b.json", _WEEK + 10, now)
    _make_aged(tmp_path / ".agent" / "receipts" / "run-a" / "plan.json", _WEEK + 10, now)
    _make_aged(tmp_path / ".agent" / "receipts" / "run-b" / "plan.json", _WEEK + 10, now)
    # An unregistered run's aged bookkeeping is still reclaimable.
    _make_aged(tmp_path / ".agent" / "completion_seen_run-dead.json", _WEEK + 10, now)

    register_active_run(tmp_path, "run-a")
    register_active_run(tmp_path, "run-b")
    results: list[int] = []
    results_lock = threading.Lock()
    try:
        def _sweep(keep_run_id: str) -> None:
            removed = sweep_agent_dir(
                tmp_path,
                keep_run_id=keep_run_id,
                now=lambda: now,
            )
            with results_lock:
                results.append(removed)

        threads = [
            threading.Thread(target=_sweep, args=("run-a",)),
            threading.Thread(target=_sweep, args=("run-b",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        # Neither registered run lost data to either sweep.
        assert (tmp_path / ".agent" / "completion_seen_run-a.json").exists()
        assert (tmp_path / ".agent" / "completion_seen_run-b.json").exists()
        assert (tmp_path / ".agent" / "receipts" / "run-a" / "plan.json").exists()
        assert (tmp_path / ".agent" / "receipts" / "run-b" / "plan.json").exists()
        # The unregistered run was reclaimed by the first sweep that saw it.
        assert not (tmp_path / ".agent" / "completion_seen_run-dead.json").exists()
        # The two sweep bodies raced for the same aged entry: each body
        # removed it when it won the race, so the total removed count is
        # at least 1 and at most one per caller.
        assert 1 <= sum(results) <= 2
    finally:
        unregister_active_run(tmp_path, "run-a")
        unregister_active_run(tmp_path, "run-b")
