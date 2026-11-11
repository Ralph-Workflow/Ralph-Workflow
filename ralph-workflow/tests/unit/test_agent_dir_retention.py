from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from ralph.mcp.artifacts.state_db import MISSING, RunStateDB
from ralph.workspace.agent_dir_retention import RetentionPassCoordinator, sweep_agent_dir

_WEEK = 7 * 24 * 3600.0


def _make_aged(path: Path, age_seconds: float, now: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    stamp = now - age_seconds
    os.utime(path, (stamp, stamp))
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
    assert (agent / "completion_seen_current.json").exists()
    assert (agent / "completion_seen_fresh.json").exists()
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
    assert (tmp_path / ".agent" / "tmp" / "development_prompt.md").exists()


def test_sweep_regression_reclaims_only_aged_codex_home_dirs(tmp_path: Path) -> None:
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


@pytest.mark.timeout_seconds(2.0)
def test_sweep_also_prunes_aged_db_rows(tmp_path: Path) -> None:
    db = RunStateDB(tmp_path)
    db.upsert_receipt("old-run", "plan", "sig")
    db.upsert_completion_sentinel("old-run", "sig")
    db.close()

    future_now = time.time() + _WEEK * 2 + 60

    sweep_agent_dir(tmp_path, keep_run_id=None, now=lambda: future_now)

    db2 = RunStateDB(tmp_path)
    assert db2.get_receipt_hmac("old-run", "plan") is MISSING
    assert db2.get_completion_sentinel_hmac("old-run") is MISSING
    db2.close()


def test_db_sweep_preserves_current_run_rows(tmp_path: Path) -> None:
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
    assert db2.get_receipt_hmac("current", "plan") == "sig-current-plan"
    assert db2.get_receipt_hmac("current", "commit_message") == ("sig-current-commit")
    assert db2.get_completion_sentinel_hmac("current") == "sig-current-sentinel"
    assert db2.get_receipt_hmac("old-run", "plan") is MISSING
    assert db2.get_completion_sentinel_hmac("old-run") is MISSING
    db2.close()


def test_runstate_db_prune_preserves_keep_run_id(tmp_path: Path) -> None:
    db = RunStateDB(tmp_path)
    db.upsert_receipt("current", "plan", "sig-c")
    db.upsert_receipt("old", "plan", "sig-o")
    db.close()

    cutoff = time.time() + 1000.0
    db2 = RunStateDB(tmp_path)
    try:
        removed = db2.prune_older_than(cutoff, keep_run_id="current")
    finally:
        db2.close()

    assert removed == 1
    db3 = RunStateDB(tmp_path)
    try:
        assert db3.get_receipt_hmac("current", "plan") == "sig-c"
        assert db3.get_receipt_hmac("old", "plan") is MISSING
    finally:
        db3.close()


def test_sweep_does_not_create_state_db_when_absent(tmp_path: Path) -> None:
    agent = tmp_path / ".agent"
    agent.mkdir(parents=True, exist_ok=True)

    assert not (tmp_path / ".agent" / "state.db").exists()

    fixed_now = time.time()
    sweep_agent_dir(tmp_path, keep_run_id=None, now=lambda: fixed_now)

    assert not (tmp_path / ".agent" / "state.db").exists()
    assert not (tmp_path / ".agent" / "state.db-wal").exists()
    assert not (tmp_path / ".agent" / "state.db-shm").exists()


def test_retention_coordinator_is_process_local(tmp_path: Path) -> None:
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
    assert coordinator.passes == 1
    other = RetentionPassCoordinator()
    assert sweep_agent_dir(tmp_path, keep_run_id=None, coordinator=other) == 0
    assert other.passes == 1


def test_sweep_consults_shared_active_run_registry(tmp_path: Path) -> None:
    now = 1_000_000_000.0
    agent_dir = tmp_path / ".agent"
    run_id = "shared-run"
    _make_aged(agent_dir / f"completion_seen_{run_id}.json", _WEEK + 10, now)
    _make_aged(agent_dir / "receipts" / run_id / "plan.json", _WEEK + 10, now)
    db = RunStateDB(tmp_path)
    db.upsert_receipt(run_id, "plan", "sig")
    db.upsert_completion_sentinel(run_id, "sig")
    db.close()
    (agent_dir / "active_runs.json").write_text('{"run_ids":["shared-run"]}', encoding="utf-8")

    sweep_agent_dir(tmp_path, keep_run_id=None, now=lambda: time.time() + _WEEK * 2)

    assert (agent_dir / f"completion_seen_{run_id}.json").exists()
    assert (agent_dir / "receipts" / run_id).exists()
    db = RunStateDB(tmp_path)
    assert db.get_receipt_hmac(run_id, "plan") == "sig"
    assert db.get_completion_sentinel_hmac(run_id) == "sig"
    db.close()


def test_sweep_consults_ownership_map_for_scratch_and_codex_home(tmp_path: Path) -> None:
    now = 1_000_000_000.0
    tmp_dir = tmp_path / ".agent" / "tmp"
    owned_scratch = tmp_dir / "agent_retry_owned.md"
    dead_scratch = tmp_dir / "agent_retry_dead.md"
    fresh_claim_scratch = tmp_dir / "agent_retry_fresh-claim.md"
    owned_home = tmp_dir / "codex-home-owned"
    dead_home = tmp_dir / "codex-home-dead"
    for path in (
        owned_scratch,
        dead_scratch,
        fresh_claim_scratch,
        owned_home / "config.toml",
        dead_home / "config.toml",
    ):
        _make_aged(path, _WEEK + 10, now)
    (tmp_path / ".agent" / "active_runs.json").write_text(
        '{"run_ids":["shared-run"]}', encoding="utf-8"
    )
    (tmp_dir / "ownership.json").write_text(
        '{"tmp/agent_retry_owned.md":{"run_id":"shared-run","created_at":1},'
        '"tmp/agent_retry_fresh-claim.md":{"run_id":"inactive-run","created_at":1000000000},'
        '"tmp/codex-home-owned":{"run_id":"shared-run","created_at":1}}',
        encoding="utf-8",
    )

    sweep_agent_dir(tmp_path, keep_run_id=None, now=lambda: now)

    assert owned_scratch.exists()
    assert fresh_claim_scratch.exists()
    assert owned_home.exists()
    assert not dead_scratch.exists()
    assert not dead_home.exists()


@pytest.mark.timeout_seconds(5)
def test_registered_active_runs_survive_parallel_sweeps(tmp_path: Path) -> None:
    import threading

    from ralph.workspace.agent_dir_retention import (
        register_active_run,
        unregister_active_run,
    )

    now = 1_000_000_000.0
    _make_aged(tmp_path / ".agent" / "completion_seen_run-a.json", _WEEK + 10, now)
    _make_aged(tmp_path / ".agent" / "completion_seen_run-b.json", _WEEK + 10, now)
    _make_aged(tmp_path / ".agent" / "receipts" / "run-a" / "plan.json", _WEEK + 10, now)
    _make_aged(tmp_path / ".agent" / "receipts" / "run-b" / "plan.json", _WEEK + 10, now)
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

        assert (tmp_path / ".agent" / "completion_seen_run-a.json").exists()
        assert (tmp_path / ".agent" / "completion_seen_run-b.json").exists()
        assert (tmp_path / ".agent" / "receipts" / "run-a" / "plan.json").exists()
        assert (tmp_path / ".agent" / "receipts" / "run-b" / "plan.json").exists()
        assert not (tmp_path / ".agent" / "completion_seen_run-dead.json").exists()
        assert 1 <= sum(results) <= 2
    finally:
        unregister_active_run(tmp_path, "run-a")
        unregister_active_run(tmp_path, "run-b")
