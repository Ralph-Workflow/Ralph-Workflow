"""Backup-reference lifecycle for auto-integrate attempts."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from pathlib import Path

from ralph.git.subprocess_runner import run_git


def qualified_backup_ref(owning_worktree: str | None, pre_feature_sha: str) -> str:
    """Worktree-qualified backup-ref name for one integration operation.

    Two linked worktrees that start from the same pre-feature SHA would
    otherwise share a single ``refs/rebase-backup/`` ref, letting one
    operation's cleanup destroy the other's only recovery ref. The
    owning-worktree hash makes the name per-operation. Deterministic in
    ``owning_worktree`` (including ``None``) so recovery can recompute
    the exact name from the durable record without schema changes.
    """
    wt8 = hashlib.sha256(str(owning_worktree).encode("utf-8")).hexdigest()[:8]
    return f"refs/rebase-backup/{wt8}-{pre_feature_sha[:8]}-{pre_feature_sha}"


def create_rebase_backup_ref(
    root: Path, pre_feature_sha: str | None, owning_worktree: str | None = None
) -> str | None:
    """Create the fail-closed backup ref that protects an in-flight feature tip."""
    if pre_feature_sha is None:
        return None
    # The operating worktree IS the owning worktree at this seam; the
    # str(root) fallback mirrors the ``record.owning_worktree or str(root)``
    # convention in _owned_landing_reintegration, so recovery recomputes
    # the identical name even before U4b threads record.owning_worktree.
    backup_name = qualified_backup_ref(
        owning_worktree if owning_worktree is not None else str(root), pre_feature_sha
    )
    try:
        result = run_git(
            ("update-ref", backup_name, pre_feature_sha),
            cwd=root,
            label="auto-integrate:backup-ref-create",
        )
    except Exception as exc:
        raise RuntimeError(
            "auto_integrate: B11/E5 backup-ref creation raised unexpectedly; "
            f"refusing to start without recovery reachability: {exc}"
        ) from exc
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout).strip()[:200]
        raise RuntimeError(
            "auto_integrate: B11/E5 backup-ref creation failed "
            f"(rc={result.returncode}): {stderr}; refusing to start"
        )
    return backup_name


def delete_rebase_backup_ref(root: Path, backup_ref: str | None) -> None:
    """Best-effort delete of a verified attempt's backup ref."""
    if backup_ref is None:
        return
    try:
        result = run_git(
            ("update-ref", "-d", backup_ref),
            cwd=root,
            label="auto-integrate:backup-ref-delete",
        )
    except Exception as exc:  # pragma: no cover -- defensive logging
        logger.warning("auto_integrate: backup-ref deletion failed: {}", exc)
        return
    if result.returncode not in (0, 1):
        logger.warning("auto_integrate: backup-ref deletion failed (rc={})", result.returncode)
