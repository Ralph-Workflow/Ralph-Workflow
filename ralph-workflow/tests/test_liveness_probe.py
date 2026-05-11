"""Tests for freshness-aware LivenessProbe with ChildLivenessRegistry."""

from __future__ import annotations

from ralph.process.child_liveness import ChildActivitySnapshot, ChildLivenessRegistry
from ralph.process.liveness import DefaultLivenessProbe, FakeLivenessProbe


def _registry(t: list[float]) -> ChildLivenessRegistry:
    return ChildLivenessRegistry(
        progress_ttl=45.0,
        heartbeat_ttl=15.0,
        stale_label_ttl=10.0,
        exit_reconcile=5.0,
        now=lambda: t[0],
    )


def test_default_probe_child_snapshot_returns_no_fresh_progress_when_registry_empty() -> None:
    t = [0.0]
    reg = _registry(t)
    probe = DefaultLivenessProbe(registry=reg)
    snap = probe.child_snapshot("scope/a")
    assert snap.has_fresh_progress is False
    assert snap.active_count == 0


def test_default_probe_child_snapshot_fresh_label_when_registered_without_heartbeat() -> None:
    t = [0.0]
    reg = _registry(t)
    reg.register_child("c1", "scope/a")
    probe = DefaultLivenessProbe(registry=reg)
    snap = probe.child_snapshot("scope/a")
    # Registered at t=0, queried at t=0: label age=0 <= stale_label_ttl=10
    assert snap.has_fresh_label is True
    assert snap.has_fresh_progress is False


def test_default_probe_child_snapshot_reports_fresh_progress_after_record_progress() -> None:
    t = [0.0]
    reg = _registry(t)
    reg.register_child("c1", "scope/a")
    t[0] = 5.0
    reg.record_progress("c1")
    probe = DefaultLivenessProbe(registry=reg)
    snap = probe.child_snapshot("scope/a")
    assert snap.has_fresh_progress is True


def test_default_probe_child_snapshot_excludes_unrelated_scope_prefixes() -> None:
    t = [0.0]
    reg = _registry(t)
    reg.register_child("c1", "scope/a")
    reg.record_progress("c1")
    probe = DefaultLivenessProbe(registry=reg)
    snap = probe.child_snapshot("scope/b")
    assert snap.has_fresh_progress is False
    assert snap.active_count == 0


def test_fake_probe_child_snapshot_active_returns_has_process() -> None:
    probe = FakeLivenessProbe(active=True)
    snap = probe.child_snapshot("scope/a")
    assert snap.has_process is True
    assert snap.has_fresh_progress is True


def test_fake_probe_child_snapshot_inactive_returns_no_process() -> None:
    probe = FakeLivenessProbe(active=False)
    snap = probe.child_snapshot("scope/a")
    assert snap.has_process is False
    assert snap.has_fresh_progress is False


def test_fake_probe_child_snapshot_with_fixed_snapshot() -> None:
    fixed = ChildActivitySnapshot(
        scope_prefix="scope/x",
        has_process=True,
        has_fresh_label=True,
        has_fresh_progress=False,
        oldest_live_child_seconds=30.0,
        active_count=2,
        terminal_count=1,
    )
    probe = FakeLivenessProbe(snapshot=fixed)
    snap = probe.child_snapshot("scope/x")
    assert snap is fixed


def test_fake_probe_any_agent_active_with_labels() -> None:
    probe = FakeLivenessProbe(active_labels=frozenset({"scope/a", "scope/b"}))
    assert probe.any_agent_active("scope/a") is True
    assert probe.any_agent_active("scope/c") is False


def test_fake_probe_child_snapshot_empty_prefix_with_active_labels_returns_no_process() -> None:
    """Empty scope_prefix must not match active_labels — mirrors DefaultLivenessProbe."""
    probe = FakeLivenessProbe(active_labels=frozenset({"agent:other-session:worker"}))
    snap = probe.child_snapshot("")
    assert snap.has_process is False
    assert snap.active_count == 0


def test_fake_probe_child_snapshot_empty_prefix_with_active_flag_returns_process() -> None:
    """Empty scope_prefix with active=True (no label set) should still return has_process."""
    probe = FakeLivenessProbe(active=True)
    snap = probe.child_snapshot("")
    assert snap.has_process is True
