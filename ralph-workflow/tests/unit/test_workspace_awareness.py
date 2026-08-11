"""Behavioral tests for bounded shared workspace awareness."""

from __future__ import annotations

from pathlib import Path

from ralph.workspace.awareness import awareness_for_workspace, release_workspace_awareness


def test_awareness_coalesces_paths_and_reports_pending(tmp_path: Path) -> None:
    awareness = awareness_for_workspace(tmp_path)
    awareness.record(str(tmp_path / "src" / "first.py"))
    awareness.record(str(tmp_path / "src" / "second.py"))
    awareness.record(str(tmp_path / "src" / "first.py"))

    assert awareness.snapshot()["freshness"] == "pending"
    assert awareness.drain() == ["src/second.py", "src/first.py"]
    assert awareness.snapshot()["freshness"] == "current"
    release_workspace_awareness(tmp_path)


def test_awareness_fallback_is_explicit_and_has_safe_next_action(tmp_path: Path) -> None:
    awareness = awareness_for_workspace(tmp_path)
    awareness.set_live_fallback("watch_capacity")

    snapshot = awareness.snapshot()

    assert snapshot["mode"] == "live_fallback"
    assert snapshot["freshness"] == "live_fallback"
    assert snapshot["cause"] == "watch_capacity"
    assert snapshot["automatic_recovery"] is True
    release_workspace_awareness(tmp_path)


def test_awareness_recovery_returns_to_watch_mode(tmp_path: Path) -> None:
    """S-3 regression: a successful observer retry is observable as recovered."""
    awareness = awareness_for_workspace(tmp_path)
    awareness.set_live_fallback("watch_capacity")

    awareness.set_watch_active()

    assert awareness.snapshot()["freshness"] == "current"
    assert awareness.snapshot()["cause"] is None
    release_workspace_awareness(tmp_path)


def test_awareness_requeues_unacknowledged_paths_without_claiming_current(tmp_path: Path) -> None:
    """S-4 regression: a failed dirty-queue handoff cannot lose an observed change."""
    awareness = awareness_for_workspace(tmp_path)
    awareness.record(str(tmp_path / "src" / "app.py"))

    drained = awareness.drain()
    awareness.requeue(drained, cause="dirty_handoff_unavailable")

    assert awareness.snapshot()["freshness"] == "pending"
    assert awareness.drain() == ["src/app.py"]
    release_workspace_awareness(tmp_path)
