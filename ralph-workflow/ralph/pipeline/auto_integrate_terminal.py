"""Verified terminal cleanup for auto-integration attempts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.auto_integrate_backup_refs import delete_rebase_backup_ref

if TYPE_CHECKING:
    from pathlib import Path
from ralph.pipeline.auto_integrate_recovery_terminal import post_attempt_verify


def verify_and_cleanup_backup(
    root: Path,
    backup_ref: str | None,
    expected_head_sha: str | None,
    owns_resolution: bool,
) -> None:
    """Verify terminal git state, then delete the no-longer-needed backup ref."""
    post_attempt_verify(
        root,
        expected_head_sha=expected_head_sha,
        owns_resolution=owns_resolution,
    )
    delete_rebase_backup_ref(root, backup_ref)
