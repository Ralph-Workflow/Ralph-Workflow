"""Tests for the run-start .agent retention sweep."""

from __future__ import annotations

import os
import time
from pathlib import Path

from ralph.mcp.artifacts.state_db import MISSING, RunStateDB
from ralph.workspace.agent_dir_retention import sweep_agent_dir

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
