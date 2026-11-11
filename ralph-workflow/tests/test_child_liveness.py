from __future__ import annotations

import pytest

from ralph.process.child_liveness import ChildLivenessRegistry

_EXPECTED_ACTIVE_CHILDREN = 2


def _registry(now_val: list[float] | None = None) -> tuple[ChildLivenessRegistry, list[float]]:
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
    assert snap.has_fresh_label is True


def test_record_heartbeat_only_advances_last_heartbeat_at_not_progress() -> None:
    reg, t = _registry()
    t[0] = 0.0
    reg.register_child("c1", "scope/a")
    t[0] = 11.0
    reg.record_heartbeat("c1")
    snap = reg.snapshot("scope/a")
    assert snap.has_fresh_label is True
    assert snap.has_fresh_progress is False


def test_record_progress_advances_both_progress_and_heartbeat() -> None:
    reg, t = _registry()
    t[0] = 0.0
    reg.register_child("c1", "scope/a")
    t[0] = 20.0
    reg.record_progress("c1", phase="running")
    snap = reg.snapshot("scope/a")
    assert snap.has_fresh_progress is True
    assert snap.has_fresh_label is True


def test_record_terminal_ack_marks_terminal_state_complete() -> None:
    reg, t = _registry()
    t[0] = 0.0
    reg.register_child("c1", "scope/a")
    t[0] = 5.0
    reg.record_terminal_ack("c1", terminal_state="complete")
    snap = reg.snapshot("scope/a")
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
    assert snap_all.active_count == 2


def test_snapshot_excludes_terminal_records_after_exit_reconcile_window() -> None:
    reg, t = _registry()
    t[0] = 0.0
    reg.register_child("c1", "scope/a")
    t[0] = 2.0
    reg.record_terminal_ack("c1", terminal_state="complete")
    snap_in = reg.snapshot("scope/a")
    assert snap_in.terminal_count == 1
    t[0] = 8.0
    snap_out = reg.snapshot("scope/a")
    assert snap_out.terminal_count == 0
    assert snap_out.active_count == 0


def test_prune_stale_drops_records_whose_progress_age_exceeds_progress_ttl() -> None:
    reg, t = _registry()
    t[0] = 0.0
    reg.register_child("c1", "scope/a")
    t[0] = 1.0
    reg.record_progress("c1")
    t[0] = 50.0
    pruned = reg.prune_stale()
    assert pruned == 1
    snap = reg.snapshot("scope/a")
    assert snap.active_count == 0


def test_prune_stale_drops_records_whose_label_age_exceeds_stale_label_ttl_with_no_progress() -> (
    None
):
    reg, t = _registry()
    t[0] = 0.0
    reg.register_child("c1", "scope/a")
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
    assert snap.oldest_live_child_seconds == pytest.approx(10.0)


def test_snapshot_aggregates_fresh_label_across_all_matching_children() -> None:
    reg, t = _registry()
    t[0] = 3.0
    reg.register_child("fresh", "scope/a")
    t[0] = 0.0
    reg.register_child("stale", "scope/a")
    t[0] = 12.0

    snap = reg.snapshot("scope/a")

    assert snap.active_count == 1
    assert snap.has_process is True
    assert snap.has_fresh_label is True


def test_snapshot_prunes_stale_label_only_records_at_read_time() -> None:
    reg, t = _registry()
    t[0] = 0.0
    reg.register_child("stale", "scope/a")
    t[0] = 15.0
    snap = reg.snapshot("scope/a")
    assert snap.active_count == 0
    assert snap.has_process is False


def test_snapshot_prunes_stale_progress_without_fresh_heartbeat() -> None:
    reg, t = _registry()
    t[0] = 0.0
    reg.register_child("child", "scope/a")
    t[0] = 1.0
    reg.record_progress("child")
    t[0] = 50.0
    snap = reg.snapshot("scope/a")
    assert snap.active_count == 0


def test_snapshot_keeps_stale_progress_when_fresh_heartbeat_exists() -> None:
    reg, t = _registry()
    t[0] = 0.0
    reg.register_child("child", "scope/a")
    t[0] = 1.0
    reg.record_progress("child")
    t[0] = 50.0
    reg.record_heartbeat("child")
    snap = reg.snapshot("scope/a")
    assert snap.active_count == 1
    assert snap.has_fresh_label is True


def test_snapshot_prunes_expired_terminal_ack_records_at_read_time() -> None:
    reg, t = _registry()
    t[0] = 0.0
    reg.register_child("child", "scope/a")
    t[0] = 1.0
    reg.record_terminal_ack("child", terminal_state="complete")
    t[0] = 7.0
    snap = reg.snapshot("scope/a")
    assert snap.terminal_count == 0
