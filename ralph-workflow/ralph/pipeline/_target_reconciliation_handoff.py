from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.merge import branch_sha
from ralph.git.operations import get_head_sha
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    clear_record,
    read_record,
    write_record,
)

if TYPE_CHECKING:
    from pathlib import Path


def finish_target_substep(root: Path, record: IntegrationRecord) -> str | None:
    if record.owning_worktree is None:
        return "target reconciliation owner missing; intervention required"
    from pathlib import Path

    if Path(record.owning_worktree).resolve() == root.resolve():
        clear_record(root)
        return None
    if record.initiating_feature_sha is None:
        return "legacy target reconciliation lacks initiating feature identity; intervention required"
    if get_head_sha(root) != record.initiating_feature_sha:
        return "initiating feature changed during target reconciliation; landing withheld"
    target_sha = branch_sha(root, record.target)
    if target_sha is None:
        return "target reconciliation completion unreadable; landing withheld"
    write_record(root, IntegrationRecord(
        phase="integrating", target=record.target,
        pre_feature_sha=record.initiating_feature_sha, pre_target_sha=target_sha,
        reintegrate_pending=True,
    ))
    return "target reconciled; initiating feature landing scheduled for recovery"


def scheduled_landing(root: Path, target: str) -> IntegrationRecord | None:
    record = read_record(root)
    return record if (
        record is not None and record.operation_kind == "feature_integrate"
        and record.reintegrate_pending and record.phase == "integrating"
        and record.target == target and get_head_sha(root) == record.pre_feature_sha
    ) else None
