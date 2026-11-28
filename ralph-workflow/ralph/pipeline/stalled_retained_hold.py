from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.git_operation_error import GitOperationError
from ralph.git.merge import is_ancestor, reset_keep
from ralph.git.operations import get_head_sha
from ralph.pipeline._integration_diagnostic_handoff import ladder_terminal_state
from ralph.pipeline._landed_predicate import landed_copy_on_target
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


def _superseded_by_feature_commits(root: Path, record: IntegrationRecord) -> bool:
    if record.is_integrated or record.integrated_feature_sha is not None:
        return False
    if record.owning_worktree is not None and record.owning_worktree != str(root):
        return False
    head = get_head_sha(root)
    if not head or head == record.pre_feature_sha:
        return False
    return is_ancestor(root, record.pre_feature_sha, head)


def _release_after_moving_to_target_copy(
    root: Path, record: IntegrationRecord, checks: int
) -> bool:
    if not _move_feature_to_target_copy(root, record):
        return False
    release_stalled_record(root)
    logger.warning(
        "retained integration made no progress for {} checks, and the target already holds "
        "{}, a rebased copy of every commit on the recorded feature tip {}; the feature "
        "branch moves to that copy, keeping uncommitted edits, and the record is released",
        checks,
        record.integrated_feature_sha,
        record.pre_feature_sha,
    )
    return True


def _move_feature_to_target_copy(root: Path, record: IntegrationRecord) -> bool:
    landed = landed_copy_on_target(root, record)
    if landed is None:
        return False
    try:
        reset_keep(root, landed)
    except GitOperationError as exc:
        logger.warning("could not move the feature branch to its landed copy {}: {}", landed, exc)
        return False
    return get_head_sha(root) == landed


def _release_if_superseded(root: Path, record: IntegrationRecord) -> bool:
    if not _superseded_by_feature_commits(root, record):
        return False
    release_stalled_record(root)
    logger.warning(
        "retained integration never landed and the feature branch has moved on from its "
        "recorded tip {} to {}; the superseded integration record is released so the "
        "current tip integrates",
        record.pre_feature_sha,
        get_head_sha(root),
    )
    return True


def release_superseded_record(root: Path) -> bool:
    try:
        record = read_record(root)
    except _RECORD_ERRORS:
        return False
    if record is None or not git_operation_is_settled(root):
        return False
    try:
        return _release_if_superseded(root, record)
    except _RECORD_ERRORS:
        return False


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
    if _release_if_superseded(root, record) or _release_after_moving_to_target_copy(
        root, record, checks
    ):
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


__all__ = ["end_stalled_retained_hold", "release_superseded_record"]
