
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ralph.files._file_snapshot import FileSnapshot
from ralph.files._file_state_issue import FileStateIssue
from ralph.files._file_state_kind import FileStateKind

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


@dataclass(frozen=True)
class FileSystemState:

    root: Path
    files: dict[Path, FileSnapshot]


def calculate_checksum(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture_file_snapshot(path: Path | str, *, root: Path | str | None = None) -> FileSnapshot:
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
    if path.is_absolute():
        return path.relative_to(root)
    return path


__all__ = [
    "DEFAULT_TRACKED_FILES",
    "FileSnapshot",
    "FileStateIssue",
    "FileStateKind",
    "FileSystemState",
    "calculate_checksum",
    "capture_file_snapshot",
    "capture_file_system_state",
    "validate_file_system_state",
]
