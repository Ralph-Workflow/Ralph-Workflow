"""Black-box tests for ChildLivenessRegistry with deterministic now-source."""

from __future__ import annotations

import pytest

from ralph.process.child_liveness import ChildLivenessRegistry

_EXPECTED_ACTIVE_CHILDREN = 2


def _registry(now_val: list[float] | None = None) -> tuple[ChildLivenessRegistry, list[float]]:
    """Build a registry with an injectable now-source."""
    t: list[float] = now_val if now_val is not None else [0.0]
    reg = ChildLivenessRegistry(
        progress_ttl=45.0,
        heartbeat_ttl=15.0,
        stale_label_ttl=10.0,
        exit_reconcile=5.0,
        now=lambda: t[0],
    )
    return reg, t


def test_register_child_creates_record_with_started_at_and_lease() -> None:
    reg, t = _registry()
    t[0] = 100.0
    reg.register_child("c1", "scope/a", pid=42, phase="spawned")
    snap = reg.snapshot("scope/a")
    assert snap.active_count == 1
    assert snap.has_process is True
    # Brand-new child: label age = 0, within stale_label_ttl
    assert snap.has_fresh_label is True


def test_record_heartbeat_only_advances_last_heartbeat_at_not_progress() -> None:
    reg, t = _registry()
    t[0] = 0.0
    reg.register_child("c1", "scope/a")
    t[0] = 11.0  # label is 11s old, beyond stale_label_ttl=10
    reg.record_heartbeat("c1")
    snap = reg.snapshot("scope/a")
    # Has fresh heartbeat -> has_fresh_label True
    assert snap.has_fresh_label is True
    # No progress recorded yet
    assert snap.has_fresh_progress is False


def test_record_progress_advances_both_progress_and_heartbeat() -> None:
    reg, t = _registry()
    t[0] = 0.0
    reg.register_child("c1", "scope/a")
    t[0] = 20.0
    reg.record_progress("c1", phase="running")
    snap = reg.snapshot("scope/a")
    assert snap.has_fresh_progress is True
    assert snap.has_fresh_label is True  # fresh heartbeat implied by progress


def test_record_terminal_ack_marks_terminal_state_complete() -> None:
    reg, t = _registry()
    t[0] = 0.0
    reg.register_child("c1", "scope/a")
    t[0] = 5.0
    reg.record_terminal_ack("c1", terminal_state="complete")
    snap = reg.snapshot("scope/a")
    # Terminal records are excluded from active counts
    assert snap.active_count == 0
    assert snap.has_process is False
    assert snap.terminal_count == 1


def test_snapshot_returns_only_records_matching_scope_prefix() -> None:
    reg, t = _registry()
    t[0] = 0.0
    reg.register_child("c1", "scope/a")
    reg.register_child("c2", "scope/b")
    snap_a = reg.snapshot("scope/a")
    snap_b = reg.snapshot("scope/b")
    snap_all = reg.snapshot("scope/")
    assert snap_a.active_count == 1
    assert snap_b.active_count == 1
    assert snap_all.active_count == 2  # noqa: PLR2004


def test_snapshot_excludes_terminal_records_after_exit_reconcile_window() -> None:
    reg, t = _registry()
    t[0] = 0.0
    reg.register_child("c1", "scope/a")
    t[0] = 2.0
    reg.record_terminal_ack("c1", terminal_state="complete")
    # Within reconcile window
    snap_in = reg.snapshot("scope/a")
    assert snap_in.terminal_count == 1
    # Advance past exit_reconcile=5.0
    t[0] = 8.0
    snap_out = reg.snapshot("scope/a")
    # Terminal count still visible in snapshot (it's a count, not filtered)
    assert snap_out.terminal_count == 1
    assert snap_out.active_count == 0


def test_prune_stale_drops_records_whose_progress_age_exceeds_progress_ttl() -> None:
    reg, t = _registry()
    t[0] = 0.0
    reg.register_child("c1", "scope/a")
    t[0] = 1.0
    reg.record_progress("c1")
    # Advance past progress_ttl=45.0
    t[0] = 50.0
    pruned = reg.prune_stale()
    assert pruned == 1
    snap = reg.snapshot("scope/a")
    assert snap.active_count == 0


def test_prune_stale_drops_records_whose_label_age_exceeds_stale_label_ttl_with_no_progress() -> None:  # noqa: E501
    reg, t = _registry()
    t[0] = 0.0
    reg.register_child("c1", "scope/a")
    # Never record progress; advance past stale_label_ttl=10.0
    t[0] = 15.0
    pruned = reg.prune_stale()
    assert pruned == 1
    snap = reg.snapshot("scope/a")
    assert snap.active_count == 0


def test_snapshot_reports_oldest_live_child_seconds() -> None:
    reg, t = _registry()
    t[0] = 0.0
    reg.register_child("c1", "scope/a")
    t[0] = 5.0
    reg.register_child("c2", "scope/a")
    t[0] = 10.0
    snap = reg.snapshot("scope/a")
    # c1 started at 0, now is 10 -> age 10; c2 started at 5, age 5
    assert snap.oldest_live_child_seconds == pytest.approx(10.0)


def test_snapshot_aggregates_fresh_label_across_all_matching_children() -> None:
    """Any fresh child label should keep the aggregate snapshot fresh.

    Regression test for timeout source-of-truth drift: the aggregate snapshot
    must answer "does any matching child still have fresh label evidence?"
    independent of record iteration order.
    """
    reg, t = _registry()
    t[0] = 3.0
    reg.register_child("fresh", "scope/a")
    t[0] = 0.0
    reg.register_child("stale", "scope/a")
    t[0] = 12.0

    snap = reg.snapshot("scope/a")

    assert snap.active_count == _EXPECTED_ACTIVE_CHILDREN
    assert snap.has_process is True
    assert snap.has_fresh_label is True
