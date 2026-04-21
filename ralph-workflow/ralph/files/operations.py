"""File capture and state-tracking helpers for Ralph checkpoints."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

DEFAULT_TRACKED_FILES: tuple[Path, ...] = (
    Path("PROMPT.md"),
    Path(".agent/PLAN.md"),
    Path(".agent/ISSUES.md"),
    Path(".agent/DEVELOPMENT_RESULT.md"),
    Path(".agent/FIX_RESULT.md"),
    Path(".agent/DEVELOPMENT_ANALYSIS_DECISION.md"),
    Path(".agent/REVIEW_ANALYSIS_DECISION.md"),
    Path(".agent/config.toml"),
    Path(".agent/start_commit"),
    Path(".agent/NOTES.md"),
    Path(".agent/status"),
)

_READ_CHUNK_SIZE = 1024 * 1024


class FileStateKind(Enum):
    """Kinds of file-state drift detected during checkpoint validation."""

    MISSING = "missing"
    UNEXPECTED = "unexpected"
    CHANGED = "changed"


@dataclass(frozen=True)
class FileSnapshot:
    """Captured state for a single tracked file."""

    path: Path
    checksum: str
    size: int
    exists: bool


@dataclass(frozen=True)
class FileStateIssue:
    """A mismatch between captured and current file state."""

    kind: FileStateKind
    path: Path


@dataclass(frozen=True)
class FileSystemState:
    """Snapshots for tracked Ralph files rooted at a workspace path."""

    root: Path
    files: dict[Path, FileSnapshot]


def calculate_checksum(path: Path | str) -> str:
    """Return the SHA-256 checksum for a file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture_file_snapshot(path: Path | str, *, root: Path | str | None = None) -> FileSnapshot:
    """Capture the current state of a file relative to a workspace root."""
    candidate = Path(path)
    workspace_root = Path(root) if root is not None else candidate.parent
    relative_path = _relative_path(candidate, workspace_root)

    if not candidate.exists():
        return FileSnapshot(path=relative_path, checksum="", size=0, exists=False)

    return FileSnapshot(
        path=relative_path,
        checksum=calculate_checksum(candidate),
        size=candidate.stat().st_size,
        exists=True,
    )


def capture_file_system_state(
    root: Path | str,
    *,
    tracked_paths: list[Path | str] | tuple[Path | str, ...] = DEFAULT_TRACKED_FILES,
) -> FileSystemState:
    """Capture snapshots for tracked files under a workspace root."""
    workspace_root = Path(root)
    snapshots = {
        relative_path: capture_file_snapshot(workspace_root / relative_path, root=workspace_root)
        for relative_path in (_relative_path(Path(path), workspace_root) for path in tracked_paths)
    }
    return FileSystemState(root=workspace_root, files=snapshots)


def validate_file_system_state(
    state: FileSystemState,
    root: Path | str | None = None,
) -> list[FileStateIssue]:
    """Compare current tracked files with a captured checkpoint snapshot."""
    workspace_root = Path(root) if root is not None else state.root
    issues: list[FileStateIssue] = []

    for relative_path, snapshot in state.files.items():
        current_snapshot = capture_file_snapshot(
            workspace_root / relative_path,
            root=workspace_root,
        )
        if snapshot.exists and not current_snapshot.exists:
            issues.append(FileStateIssue(kind=FileStateKind.MISSING, path=relative_path))
            continue
        if not snapshot.exists and current_snapshot.exists:
            issues.append(FileStateIssue(kind=FileStateKind.UNEXPECTED, path=relative_path))
            continue
        if (
            snapshot.exists
            and current_snapshot.exists
            and current_snapshot.checksum != snapshot.checksum
        ):
            issues.append(FileStateIssue(kind=FileStateKind.CHANGED, path=relative_path))

    return issues


def _relative_path(path: Path, root: Path) -> Path:
    """Normalize a path to be relative to the workspace root when possible."""
    if path.is_absolute():
        return path.relative_to(root)
    return path
