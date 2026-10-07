"""Resume target reconciliation in its owning worktree without moving the feature."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.merge import MERGE_STATE_IN_PROGRESS, branch_sha, merge_state
from ralph.git.rebase.rebase_continuation import rebase_in_progress_at
from ralph.pipeline._integration_continuation import (
    _legacy_completed_rebase,
    active_operation_matches,
    continue_retained_resolution,
)
from ralph.pipeline._pending_rebase_continue import resume_pending_rebase
from ralph.pipeline._target_reconciliation_handoff import finish_target_substep
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    bind_integration_record_root,
    read_record,
    write_record,
)
from ralph.pipeline.auto_integrate_transaction import integration_transaction
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver


def recover_target_resolution(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> RebaseState:
    """Bind resolver receipts to their durable owner while leasing the Git owner."""
    if record.owning_worktree is None:
        return _retained(record, "target reconciliation has no owning worktree")
    owner = Path(record.owning_worktree)
    foreign = owner.resolve() != root.resolve()
    lease = integration_transaction(owner) if foreign else nullcontext(True)
    with lease as acquired:
        if not acquired or (foreign and read_record(owner) is not None):
            return _retained(record, "target reconciliation worktree busy; recovery will retry")
        if record.diagnostic_evidence:
            from ralph.pipeline._integration_diagnostic import diagnostic_evidence_unchanged

            if not diagnostic_evidence_unchanged(owner, record):
                return _retained(
                    record,
                    "diagnostic evidence changed; original evidence must be restored before recovery",
                )
            record = record.model_copy(
                update={"diagnostic_evidence": (), "diagnostic_ownership": None}
            )
            write_record(root, record)
        with bind_integration_record_root(owner, root):
            continued = _continue_target_record(owner, root, record, config, conflict_resolver, rebase_stop_resolver)
            if isinstance(continued, str) or branch_sha(owner, record.target) != continued.integrated_feature_sha:
                return _retained(record, continued if isinstance(continued, str) else "target reconciliation completion does not match target")
            reason = finish_target_substep(root, record)
            if reason is not None:
                return _retained(record, reason)
    return RebaseState(
        last_action="recovered",
        last_target=record.target,
        last_reason="completed retained target reconciliation",
        fast_forwarded=False,
    )


def _continue_target_record(
    owner: Path, root: Path, record: IntegrationRecord, config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None, rebase_stop_resolver: RebaseStopResolver | None,
) -> IntegrationRecord | str:
    if (
        not (record.resolving_rebase or record.resolving_merge or record.rebase_continue_pending)
        and (rebase_in_progress_at(owner) or merge_state(owner) == MERGE_STATE_IN_PROGRESS)
    ):
        if not active_operation_matches(owner, record):
            return "target operation identity differs from retained ownership"
        record = record.model_copy(update={
            "resolving_rebase": rebase_in_progress_at(owner),
            "resolving_merge": merge_state(owner) == MERGE_STATE_IN_PROGRESS,
        })
        write_record(root, record)
    elif record.phase == "integrating" and not record.rebase_continue_pending:
        completed = _legacy_completed_rebase(owner, record.model_copy(update={"resolving_rebase": True}))
        if completed is not None:
            write_record(root, completed)
            record = completed
    return (
        resume_pending_rebase(owner, record) if record.rebase_continue_pending
        else continue_retained_resolution(owner, record, config, conflict_resolver, rebase_stop_resolver)
    )


def _retained(record: IntegrationRecord, reason: str) -> RebaseState:
    return RebaseState(
        last_action="skipped",
        last_target=record.target,
        last_reason=reason,
        recovery_record_retained=True,
        fast_forwarded=False,
    )
