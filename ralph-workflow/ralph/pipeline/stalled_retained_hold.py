from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.pipeline._integration_diagnostic_handoff import ladder_terminal_state
from ralph.pipeline._terminal_emitter import critical
from ralph.pipeline.auto_integrate_backup_refs import qualified_backup_ref
from ralph.pipeline.auto_integrate_record import read_record, release_stalled_record
from ralph.pipeline.auto_integrate_recovery_record import (
    git_operation_is_settled,
    recorded_landing_is_contained_in_target,
)
from ralph.pipeline.auto_integrate_recovery_terminal import (
    delete_rebase_backup_refs,
    record_resolution_attempt_key,
    terminal_marker_blocks_attempt,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.auto_integrate_record import IntegrationRecord

_RECORD_ERRORS = (OSError, RuntimeError, ValueError, KeyError)


def _ladder_is_terminal(root: Path, record: IntegrationRecord) -> bool:
    if terminal_marker_blocks_attempt(record, record_resolution_attempt_key(record)):
        return True
    return ladder_terminal_state(root, record) is not None


def end_stalled_retained_hold(root: Path, *, checks: int) -> bool:
    try:
        record = read_record(root)
    except _RECORD_ERRORS:
        return False
    if record is None or not git_operation_is_settled(root):
        return False
    if recorded_landing_is_contained_in_target(root, record):
        delete_rebase_backup_refs(
            root,
            qualified_backup_ref(record.owning_worktree or str(root), record.pre_feature_sha),
        )
        release_stalled_record(root)
        logger.warning(
            "retained integration made no progress for {} checks, and the target already "
            "contains the recorded work; the stale integration record is released",
            checks,
        )
        return True
    if not _ladder_is_terminal(root, record):
        return False
    release_stalled_record(root)
    critical(
        "retained integration made no progress for {} checks after its ladder reached a "
        "terminal state; the integration record is released so a fresh integration starts, "
        "and the pre-integration feature tip {} stays at {}",
        checks,
        record.pre_feature_sha,
        qualified_backup_ref(record.owning_worktree or str(root), record.pre_feature_sha),
    )
    return True


__all__ = ["end_stalled_retained_hold"]
