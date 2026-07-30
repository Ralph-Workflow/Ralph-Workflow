"""Backup-reference lifecycle for auto-integration attempts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from pathlib import Path

from ralph.git.subprocess_runner import run_git


def create_rebase_backup_ref(root: Path, pre_feature_sha: str | None) -> str | None:
    """Create the fail-closed backup ref that protects an in-flight feature tip."""
    if pre_feature_sha is None:
        return None
    backup_name = f"refs/rebase-backup/{pre_feature_sha[:8]}-{pre_feature_sha}"
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
