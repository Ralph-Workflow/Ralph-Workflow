"""Recover interrupted resolver path ownership without sweeping unrelated edits."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.subprocess_runner import run_git
from ralph.pipeline._git_operation_authority import GitOperationKind, classify_git_operation
from ralph.pipeline.auto_integrate_record import (
    MARK_RESOLVING_MERGE,
    read_record,
    write_record,
)

if TYPE_CHECKING:
    from pathlib import Path


def original_conflict_paths(root: Path) -> tuple[str, ...]:
    """Read Git's resolve-undo receipt after staging removed conflict entries."""
    result = run_git(
        ("ls-files", "--resolve-undo", "-z"), cwd=root, label="resolution:original-conflicts"
    )
    if result.returncode:
        return ()
    return tuple(
        sorted({entry.partition("\t")[2] for entry in result.stdout.split("\0") if "\t" in entry})
    )


def retained_merge_paths(root: Path, paths: tuple[str, ...]) -> tuple[str, ...]:
    """Use only durable or Git-recorded conflict paths from the active merge."""
    record = read_record(root)
    if record is None or classify_git_operation(record) is not GitOperationKind.RESOLVING_MERGE:
        return paths
    return tuple(
        sorted(set(paths) | set(record.resolving_paths) | set(original_conflict_paths(root)))
    )


def retained_rebase_paths(root: Path, sha: str, paths: tuple[str, ...]) -> tuple[str, ...]:
    """Keep resolver scope pinned to the stopped commit, never a prior replay."""
    record = read_record(root)
    if record is None or classify_git_operation(record) is not GitOperationKind.RESOLVING_REBASE:
        return paths
    saved = record.resolving_paths if record.resolving_stop_sha == sha else ()
    scope = tuple(sorted(set(paths) | set(saved) | set(original_conflict_paths(root))))
    if scope:
        write_record(
            root,
            record.with_transition(
                MARK_RESOLVING_MERGE,
                resolving_paths=scope,
                resolving_stop_sha=sha,
            ),
        )
    return scope
