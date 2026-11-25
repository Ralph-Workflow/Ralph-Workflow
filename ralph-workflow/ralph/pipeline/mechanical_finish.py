from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.subprocess_runner import run_git

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

__all__ = [
    "mechanical_finish",
    "merged_commit_paths",
    "replayed_commit_paths",
    "unstaged_resolution_edits",
]


def mechanical_finish(root: Path, target: str) -> bool:
    from ralph.git.operation_snapshot import OperationState, observe_operation_snapshot
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.conflict_resolution.rebase_loop import (
        current_rebase_identity,
        finish_resolved_rebase_stops,
    )

    snapshot = observe_operation_snapshot(root, target=target)
    if snapshot.operation_state is not OperationState.REBASE_PAUSED:
        return False
    if not snapshot.unmerged_query_ok or snapshot.unmerged_paths:
        return False
    record = read_record(root)
    if record is not None and record.resolving_rebase:
        owned = current_rebase_identity(root)
        if owned != (record.pre_feature_sha, record.pre_target_sha):
            return False
        scope: tuple[str, ...] = tuple(record.resolving_paths) or replayed_commit_paths(root)
        pending: tuple[str, ...] = unstaged_resolution_edits(root, scope)
        if pending:
            logger.info(
                "integration resolution: resolving path(s) {} carry unstaged edits from an "
                "interrupted resolution; handing them to the resolver instead of finishing",
                ", ".join(pending),
            )
            return False
    try:
        return finish_resolved_rebase_stops(root, target)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        logger.warning("integration resolution: mechanical finish failed: {}", exc)
        return False


def _ref_exists(root: Path, ref: str, label: str) -> bool:
    return run_git(("rev-parse", "--verify", "--quiet", ref), cwd=root, label=label).returncode == 0


def replayed_commit_paths(root: Path) -> tuple[str, ...]:
    if not _ref_exists(root, "REBASE_HEAD", "integration-resolution-rebase-head"):
        return ()
    result = run_git(
        ("diff-tree", "-r", "--name-only", "--no-commit-id", "-z", "--root", "REBASE_HEAD"),
        cwd=root,
        label="integration-resolution-replayed-paths",
    )
    if result.returncode != 0:
        return ()
    return tuple(path for path in result.stdout.split("\0") if path)


def merged_commit_paths(root: Path) -> tuple[str, ...]:
    if not _ref_exists(root, "MERGE_HEAD", "integration-resolution-merge-head"):
        return ()
    base = run_git(
        ("merge-base", "HEAD", "MERGE_HEAD"),
        cwd=root,
        label="integration-resolution-merge-base",
    )
    if base.returncode != 0 or not base.stdout.strip():
        return ()
    result = run_git(
        (
            "diff-tree",
            "-r",
            "--name-only",
            "--no-commit-id",
            "-z",
            base.stdout.strip(),
            "MERGE_HEAD",
        ),
        cwd=root,
        label="integration-resolution-merged-paths",
    )
    if result.returncode != 0:
        return ()
    return tuple(path for path in result.stdout.split("\0") if path)


def unstaged_resolution_edits(root: Path, resolving_paths: Sequence[str]) -> tuple[str, ...]:
    if not resolving_paths:
        return ()
    paths: list[str] = list(resolving_paths)
    result = run_git(
        ("diff", "--name-only", "-z", "--", *paths),
        cwd=root,
        label="integration-resolution-unstaged-edits",
    )
    if result.returncode != 0:
        return tuple(paths)
    changed: list[str] = [path for path in result.stdout.split("\0") if path]
    return tuple(changed)
