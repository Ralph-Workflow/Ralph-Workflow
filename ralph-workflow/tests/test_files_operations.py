"""Unit tests for file capture and state tracking operations."""

from __future__ import annotations

import importlib
from pathlib import Path

operations = importlib.import_module("ralph.files.operations")

DEFAULT_TRACKED_FILES = operations.DEFAULT_TRACKED_FILES
FileStateKind = operations.FileStateKind
calculate_checksum = operations.calculate_checksum
capture_file_snapshot = operations.capture_file_snapshot
capture_file_system_state = operations.capture_file_system_state
validate_file_system_state = operations.validate_file_system_state


def test_calculate_checksum_uses_sha256(tmp_path: Path) -> None:
    """Checksum calculation should produce stable SHA-256 hex digests."""
    target = tmp_path / "sample.txt"
    target.write_text("hello world", encoding="utf-8")

    assert (
        calculate_checksum(target)
        == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    )


def test_capture_file_snapshot_for_existing_file(tmp_path: Path) -> None:
    """Capturing a file snapshot should record path, size, and checksum."""
    target = tmp_path / "tracked.txt"
    target.write_text("tracked content", encoding="utf-8")

    snapshot = capture_file_snapshot(target, root=tmp_path)

    assert snapshot.path == Path("tracked.txt")
    assert snapshot.exists is True
    assert snapshot.size == len(b"tracked content")
    assert snapshot.checksum == calculate_checksum(target)


def test_capture_file_snapshot_for_missing_file(tmp_path: Path) -> None:
    """Capturing a missing file should preserve relative path and missing state."""
    snapshot = capture_file_snapshot(tmp_path / "missing.txt", root=tmp_path)

    assert snapshot.path == Path("missing.txt")
    assert snapshot.exists is False
    assert snapshot.size == 0
    assert snapshot.checksum == ""


def test_capture_file_system_state_uses_default_tracked_files(tmp_path: Path) -> None:
    """File-system capture should snapshot the default Ralph checkpoint files."""
    prompt = tmp_path / "PROMPT.md"
    prompt.write_text("# task", encoding="utf-8")
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "PLAN.md").write_text("- [ ] step", encoding="utf-8")

    state = capture_file_system_state(tmp_path)

    assert tuple(state.files) == DEFAULT_TRACKED_FILES
    assert state.files[Path("PROMPT.md")].exists is True
    assert state.files[Path(".agent/PLAN.md")].exists is True
    assert state.files[Path(".agent/ISSUES.md")].exists is False


def test_validate_file_system_state_reports_missing_changed_and_unexpected_files(
    tmp_path: Path,
) -> None:
    """State validation should classify missing, changed, and unexpected files."""
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("before", encoding="utf-8")

    deleted = tmp_path / "deleted.txt"
    deleted.write_text("gone later", encoding="utf-8")

    state = capture_file_system_state(
        tmp_path,
        tracked_paths=[tracked, deleted, tmp_path / "new.txt"],
    )

    tracked.write_text("after", encoding="utf-8")
    deleted.unlink()
    (tmp_path / "new.txt").write_text("new file", encoding="utf-8")

    issues = validate_file_system_state(state, tmp_path)

    assert [(issue.kind, issue.path) for issue in issues] == [
        (FileStateKind.CHANGED, Path("tracked.txt")),
        (FileStateKind.MISSING, Path("deleted.txt")),
        (FileStateKind.UNEXPECTED, Path("new.txt")),
    ]
