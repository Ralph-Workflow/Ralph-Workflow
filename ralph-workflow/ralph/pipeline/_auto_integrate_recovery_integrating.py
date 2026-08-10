"""Restore owned interrupted integrations without overwriting a moved target."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import MERGE_STATE_NONE, branch_sha
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.pipeline.auto_integrate_record import IntegrationRecord


def recover_integrating_record(
    *,
    root: Path,
    record: IntegrationRecord,
    operation_kind: str,
    operation_root: Path,
    abort_failed: bool,
    merge_state: Callable[[Path], str],
    reset_hard: Callable[[Path, str], None],
    rebase_in_progress: Callable[[Path], bool],
    head_matches_sha: Callable[[Path, str], bool],
    clear_record: Callable[[Path], None],
) -> RebaseState:
    """Restore an interrupted integration after preserving any moved target."""
    if operation_kind == "target_reconcile":
        try:
            target_sha = branch_sha(operation_root, record.target)
        except Exception as exc:
            return _retained(record, f"could not read target: {exc}")
        if target_sha != record.pre_target_sha:
            return _retained(record, "target moved during reconciliation")
    reset_failed = False
    try:
        reset_hard(operation_root, record.pre_feature_sha)
    except Exception as exc:
        reset_failed = True
        logger.warning("recovery: reset_hard failed: {}", exc)
    if (
        abort_failed
        or reset_failed
        or rebase_in_progress(operation_root)
        or merge_state(operation_root) != MERGE_STATE_NONE
        or not head_matches_sha(operation_root, record.pre_feature_sha)
    ):
        return _retained(record, "feature branch not restored")
    clear_record(root)
    return RebaseState(
        last_action="recovered",
        last_reason=(
            "restored target worktree after interrupted reconciliation"
            if operation_kind == "target_reconcile"
            else "restored feature branch after interrupted rebase"
        ),
        last_target=record.target,
        fast_forwarded=False,
    )


def _retained(record: IntegrationRecord, detail: str) -> RebaseState:
    """Return the durable retry state for an unprovable restoration."""
    return RebaseState(
        last_action="skipped",
        last_reason=f"recovery: {detail}, record retained for retry",
        last_target=record.target,
        fast_forwarded=False,
        recovery_record_retained=True,
    )
