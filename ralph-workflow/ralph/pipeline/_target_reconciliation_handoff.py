from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.merge import branch_sha
from ralph.git.operations import get_head_sha
from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline._integration_owner_seam import decide_seam_action
from ralph.pipeline.auto_integrate_record import (
    MARK_REINTEGRATE_PENDING,
    START_INTEGRATING,
    IntegrationRecord,
    clear_record,
    read_record,
    write_record,
)
from ralph.pipeline.integration_director import DEFAULT_REMOTE

if TYPE_CHECKING:
    from pathlib import Path


_ADOPTED_ACTIONS: frozenset[IntegrationAction] = frozenset(
    {
        IntegrationAction.RESOLVE_ACTIVE_OPERATION,
        IntegrationAction.REINTEGRATE_AGAINST_TARGET,
        IntegrationAction.LAND_TARGET,
    }
)


def finish_target_substep(root: Path, record: IntegrationRecord) -> str | None:
    if record.owning_worktree is None:
        return "target reconciliation owner missing; intervention required"
    from pathlib import Path

    if Path(record.owning_worktree).resolve() == root.resolve():
        clear_record(root)
        return None
    if record.initiating_feature_sha is None:
        return (
            "legacy target reconciliation lacks initiating feature identity; intervention required"
        )
    if get_head_sha(root) != record.initiating_feature_sha:
        return "initiating feature changed during target reconciliation; landing withheld"
    target_sha = branch_sha(root, record.target)
    if target_sha is None:
        return "target reconciliation completion unreadable; landing withheld"
    scheduled = record.with_transition(
        START_INTEGRATING,
        phase="integrating",
        target=record.target,
        pre_feature_sha=record.initiating_feature_sha,
        pre_target_sha=target_sha,
        operation_kind="feature_integrate",
        owning_worktree=None,
        initiating_feature_sha=None,
    ).with_transition(
        MARK_REINTEGRATE_PENDING,
        reintegrate_pending=True,
    )
    write_record(root, scheduled)
    return "target reconciled; initiating feature landing scheduled for recovery"


def scheduled_landing(root: Path, target: str) -> IntegrationRecord | None:
    record = read_record(root)
    if record is None:
        return None
    if record.operation_kind != "feature_integrate" or not record.reintegrate_pending:
        return None
    if record.target != target:
        return None
    if get_head_sha(root) != record.pre_feature_sha:
        return None
    action = decide_seam_action(
        root,
        record=record,
        agent_active=False,
        ledger_path=None,
        target=target,
        remote=DEFAULT_REMOTE,
    )
    return record if action in _ADOPTED_ACTIONS else None


__all__ = [
    "finish_target_substep",
    "scheduled_landing",
]
