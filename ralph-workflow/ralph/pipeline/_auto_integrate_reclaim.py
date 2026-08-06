"""Fail-closed reclamation of a dirty target-owning worktree."""

from __future__ import annotations

from datetime import UTC, datetime
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.git.merge import WORKTREE_QUERY_FAILED, merge_in_progress, worktree_lookup
from ralph.git.operations import find_main_worktree_root
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.git.subprocess_runner import run_git

if TYPE_CHECKING:
    from pathlib import Path


def target_worktree_lookup(repo_root: Path, target: str) -> tuple[str, Path | None]:
    """Resolve the sole worktree that owns ``target``, failing closed on lookup errors."""
    try:
        return worktree_lookup(find_main_worktree_root(repo_root), target)
    except Exception:
        return WORKTREE_QUERY_FAILED, None


def reclaim_dirty_target_worktree(worktree: Path, target: str) -> str | None:
    """Snapshot and discard dirty target-owner state, returning its durable ref."""
    if merge_in_progress(worktree) or rebase_in_progress(worktree):
        return None
    status = run_git(
        ("status", "--porcelain", "-z"), cwd=worktree, label="auto-integrate:reclaim-status"
    )
    if status.returncode != 0 or not status.stdout:
        return None
    snapshot_ref = _create_snapshot(worktree, target)
    if snapshot_ref is None:
        return None
    if not _discard_dirty_state(worktree):
        return None
    discarded = len(status.stdout.rstrip("\0").split("\0"))
    logger.warning(
        "auto_integrate: reclaimed dirty target worktree {} into {}; discarded {} path(s). "
        "Recover with: git -C {} checkout {} -- .",
        worktree,
        snapshot_ref,
        discarded,
        worktree,
        snapshot_ref,
    )
    return snapshot_ref


def _create_snapshot(worktree: Path, target: str) -> str | None:
    """Create a reclaim ref without leaving the owner's index staged on failure."""
    staged = run_git(
        ("diff", "--cached", "--binary", "--full-index"),
        cwd=worktree,
        label="auto-integrate:reclaim-save-index",
    )
    if staged.returncode != 0:
        return None
    if run_git(("add", "-A"), cwd=worktree, label="auto-integrate:reclaim-stage").returncode != 0:
        return None
    tree = run_git(("write-tree",), cwd=worktree, label="auto-integrate:reclaim-write-tree")
    if tree.returncode != 0 or not (tree_sha := tree.stdout.strip()):
        _restore_index(worktree, staged.stdout)
        return None
    commit = run_git(
        (*COMMIT_PIN_CONFIG_ARGS, "commit-tree", tree_sha, "-p", "HEAD", "-m", "ralph reclaim snapshot"),
        cwd=worktree,
        label="auto-integrate:reclaim-commit",
    )
    if commit.returncode != 0 or not (snapshot_sha := commit.stdout.strip()):
        _restore_index(worktree, staged.stdout)
        return None
    snapshot_ref = (
        f"refs/ralph-reclaim/{target}/"
        f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{snapshot_sha[:8]}"
    )
    result = run_git(
        ("update-ref", snapshot_ref, snapshot_sha), cwd=worktree, label="auto-integrate:reclaim-ref"
    )
    if result.returncode != 0:
        _restore_index(worktree, staged.stdout)
        return None
    return snapshot_ref


def _restore_index(worktree: Path, staged_patch: str) -> None:
    """Restore the pre-snapshot index while preserving working-tree bytes."""
    if run_git(("read-tree", "HEAD"), cwd=worktree, label="auto-integrate:reclaim-restore-index").returncode != 0:
        return
    if not staged_patch:
        return
    with NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".patch") as patch:
        patch.write(staged_patch)
        patch.flush()
        run_git(
            ("apply", "--cached", "--binary", patch.name),
            cwd=worktree,
            label="auto-integrate:reclaim-restore-staged",
        )


def _discard_dirty_state(worktree: Path) -> bool:
    reset = run_git(("reset", "--hard", "HEAD"), cwd=worktree, label="auto-integrate:reclaim-reset")
    clean = run_git(("clean", "-fd"), cwd=worktree, label="auto-integrate:reclaim-clean")
    return reset.returncode == 0 and clean.returncode == 0


__all__ = ["reclaim_dirty_target_worktree", "target_worktree_lookup"]
