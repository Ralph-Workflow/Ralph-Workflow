
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.pipeline._auto_integrate_record_restamp import is_owner_dead
from ralph.pipeline._git_operation_authority import GitOperationKind, classify_git_operation
from ralph.pipeline.auto_integrate_record import parse_lease
from ralph.pipeline.auto_integrate_transition import DIAGNOSTIC_BUDGET_SPENT_REASON
from ralph.pipeline.operation_evidence import (
    EvidenceVerdict,
    SubprocessOperationEvidenceCollector,
    identity_fingerprint,
)

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.operation_evidence import OperationEvidenceCollector

DIAGNOSTIC_REPAIR_ATTEMPT_CAP: int = 5

__all__ = [
    "DIAGNOSTIC_BUDGET_SPENT_REASON",
    "DIAGNOSTIC_REPAIR_ATTEMPT_CAP",
    "EVIDENCE_LEASE_SECONDS",
    "diagnostic_evidence_unchanged",
    "diagnostic_evidence_verdict",
    "diagnostic_needed",
    "evaluate_held_diagnostic_evidence",
    "operation_evidence_collector",
    "release_verified_diagnostic",
]

EVIDENCE_LEASE_SECONDS: float = 1800.0

_OPERATION_EVIDENCE_COLLECTOR: list[OperationEvidenceCollector] = [
    SubprocessOperationEvidenceCollector()
]


def operation_evidence_collector() -> OperationEvidenceCollector:
    return _OPERATION_EVIDENCE_COLLECTOR[0]


def _operation_root_for(record: IntegrationRecord, default_root: Path) -> Path:
    if (
        record.operation_kind == "target_reconcile"
        and record.owning_worktree
        and DEFAULT_FILE_BACKEND.exists(Path(record.owning_worktree))
    ):
        return Path(record.owning_worktree)
    return default_root


def diagnostic_evidence_verdict(
    root: Path,
    record: IntegrationRecord,
    collector: OperationEvidenceCollector | None = None,
) -> EvidenceVerdict:
    if not record.diagnostic_evidence and not record.diagnostic_ownership:
        return EvidenceVerdict.UNCHANGED
    if is_owner_dead(record) or record.diagnostic_ownership is None:
        return EvidenceVerdict.UNKNOWN
    fingerprint, held_at = parse_lease(record.diagnostic_ownership)
    is_expired = held_at is not None and (time.time() - held_at) > EVIDENCE_LEASE_SECONDS
    if is_expired or fingerprint is None:
        return EvidenceVerdict.UNKNOWN
    if fingerprint != identity_fingerprint(record):
        return EvidenceVerdict.CHANGED
    return EvidenceVerdict.UNCHANGED


evaluate_held_diagnostic_evidence = diagnostic_evidence_verdict


def diagnostic_evidence_unchanged(
    root: Path,
    record: IntegrationRecord,
    collector: OperationEvidenceCollector | None = None,
) -> bool:
    return (
        diagnostic_evidence_verdict(root, record, collector=collector) == EvidenceVerdict.UNCHANGED
    )


def release_verified_diagnostic(
    root: Path,
    record: IntegrationRecord,
    collector: OperationEvidenceCollector | None = None,
) -> EvidenceVerdict:
    try:
        return diagnostic_evidence_verdict(root, record, collector=collector)
    except Exception:
        return EvidenceVerdict.UNKNOWN


def diagnostic_needed(root: Path, record: IntegrationRecord) -> bool:
    from ralph.git.merge import MERGE_STATE_NONE, MERGE_STATE_UNKNOWN, merge_state, unmerged_paths
    from ralph.git.rebase.rebase_continuation import rebase_in_progress_at
    from ralph.pipeline._retained_resolution_scope import original_conflict_paths

    if record.diagnostic_evidence:
        return True
    operation_root = Path(record.owning_worktree) if record.owning_worktree else root
    try:
        if classify_git_operation(record) is GitOperationKind.AWAITING_CONTINUE:
            from ralph.pipeline._pending_rebase_continue import pending_rebase_identity_matches

            return not pending_rebase_identity_matches(operation_root, record)
        if classify_git_operation(record) is GitOperationKind.AWAITING_MERGE_COMMIT:
            from ralph.pipeline._pending_merge_commit import pending_merge_identity_matches

            return not pending_merge_identity_matches(operation_root, record)
        unmerged = unmerged_paths(operation_root)
        unreadable = "<unmerged-path-query-failed>" in unmerged
        missing_scope = (record.resolving_merge or record.resolving_rebase) and not (
            record.resolving_paths or unmerged or original_conflict_paths(operation_root)
        )
        if unreadable or missing_scope:
            return True
        merge = merge_state(operation_root)
        return merge == MERGE_STATE_UNKNOWN or (
            merge == MERGE_STATE_NONE and not rebase_in_progress_at(operation_root)
        )
    except Exception:
        return True
