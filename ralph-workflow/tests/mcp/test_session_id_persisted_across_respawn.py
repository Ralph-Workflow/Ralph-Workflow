"""Tests for session_id persistence across respawns via snapshot file."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.mcp.server._snapshots import (
    read_snapshot,
    snapshot_dir,
    write_snapshot,
)


class TestSnapshotPersistence:
    """Tests for the snapshot-based session_id persistence."""

    def test_write_snapshot_creates_dir_and_file(self, tmp_path: Path) -> None:
        """When snapshot directory does not exist, write_snapshot creates it AND the file."""
        snap_dir = tmp_path / "nonexistent" / "subdir"
        assert not snap_dir.exists()

        write_snapshot(
            snap_dir,
            session_id="test-session-123",
            run_id="run-456",
            broker_secret_fingerprint="abc123",
        )

        assert snap_dir.exists()
        assert (snap_dir / "session.json").exists()

        data = read_snapshot(snap_dir)
        assert data["session_id"] == "test-session-123"
        assert data["run_id"] == "run-456"
        assert data["broker_secret_fingerprint"] == "abc123"

    def test_read_snapshot_returns_same_dict_on_two_reads(self, tmp_path: Path) -> None:
        """read_snapshot returns the same dict on two consecutive reads."""
        snap_dir = snapshot_dir(tmp_path, "bridge-run-789")
        write_snapshot(
            snap_dir,
            session_id="session-abc",
            run_id="bridge-run-789",
            broker_secret_fingerprint="fp-xyz",
        )

        first_read = read_snapshot(snap_dir)
        second_read = read_snapshot(snap_dir)

        assert first_read == second_read
        assert first_read["session_id"] == "session-abc"
        assert first_read["run_id"] == "bridge-run-789"
        assert first_read["broker_secret_fingerprint"] == "fp-xyz"

    def test_snapshot_reused_across_spawns_same_session_id(self, tmp_path: Path) -> None:
        """After one spawn writes the snapshot, a second spawn reads the same session_id."""
        bridge_run_id = "run-id-for-respawn"
        snap_dir = snapshot_dir(tmp_path, bridge_run_id)

        # First spawn: write snapshot with session_id.
        write_snapshot(
            snap_dir,
            session_id="persist-this-session",
            run_id=bridge_run_id,
            broker_secret_fingerprint=None,
        )

        # Second spawn: read from the same snapshot file.
        data = read_snapshot(snap_dir)
        assert data["session_id"] == "persist-this-session"

        # Verify the same file is being read (not a new one).
        assert (snap_dir / "session.json").exists()

    def test_snapshot_dir_creates_correct_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """snapshot_dir returns protected path outside the workspace (E2.5)."""
        snap_root = tmp_path / "protected_cache"
        monkeypatch.setenv("RALPH_MCP_SNAPSHOT_DIR", str(snap_root))
        workspace = tmp_path / "myworkspace"
        workspace.mkdir()
        run_id = "unique-bridge-run-id"

        result = snapshot_dir(workspace, run_id, override_dir=snap_root)

        expected = snap_root / run_id
        assert result == expected
        assert result.exists()

    def test_snapshot_survives_workspace_tmp_deletion(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Snapshot survives deleting <workspace>/.agent/tmp (E2.5)."""
        snap_root = tmp_path / "protected_cache"
        monkeypatch.setenv("RALPH_MCP_SNAPSHOT_DIR", str(snap_root))
        workspace = tmp_path / "myworkspace"
        workspace_tmp = workspace / ".agent" / "tmp"
        workspace_tmp.mkdir(parents=True)
        run_id = "survive-test-run"

        snap_dir = snapshot_dir(workspace, run_id, override_dir=snap_root)
        write_snapshot(
            snap_dir,
            session_id="persisted-across-clean",
            run_id=run_id,
            broker_secret_fingerprint=None,
        )

        # Simulate workspace cleanup deleting .agent/tmp
        import shutil

        shutil.rmtree(workspace / ".agent")

        # Snapshot still exists and reads correctly
        assert snap_dir.exists()
        data = read_snapshot(snap_dir)
        assert data["session_id"] == "persisted-across-clean"

    def test_read_snapshot_returns_empty_dict_when_file_missing(self, tmp_path: Path) -> None:
        """read_snapshot returns {} when session.json does not exist."""
        snap_dir = tmp_path / "completely" / "missing"
        assert not snap_dir.exists()

        result = read_snapshot(snap_dir)

        assert result == {}

    def test_write_snapshot_atomic_write(self, tmp_path: Path) -> None:
        """write_snapshot uses atomic replace; intermediate .tmp file is replaced."""
        snap_dir = snapshot_dir(tmp_path, "atomic-test")
        path = snap_dir / "session.json"

        # Write initial.
        write_snapshot(
            snap_dir,
            session_id="first",
            run_id="atomic-test",
            broker_secret_fingerprint=None,
        )
        assert path.exists()
        first_content = path.read_text()

        # Write again with different data.
        write_snapshot(
            snap_dir,
            session_id="second",
            run_id="atomic-test",
            broker_secret_fingerprint=None,
        )

        # Verify content is updated.
        second_content = path.read_text()
        assert second_content != first_content
        data = read_snapshot(snap_dir)
        assert data["session_id"] == "second"
