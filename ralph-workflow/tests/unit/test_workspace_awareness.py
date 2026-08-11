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
