from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.pipeline import _terminal_emitter as terminal_emitter
from ralph.pipeline._auto_integrate_record_restamp import is_owner_dead
from ralph.pipeline._git_operation_authority import GitOperationKind, classify_git_operation
from ralph.pipeline._integration_diagnostic_evidence import EVIDENCE_LEASE_SECONDS
from ralph.pipeline._integration_diagnostic_handoff import escalate_diagnostic_budget_to_ladder
from ralph.pipeline._integration_diagnostic_state import DIAGNOSTIC_REPAIR_ATTEMPT_CAP
from ralph.pipeline.auto_integrate_record import (
    RELEASE_DIAGNOSTIC_EVIDENCE,
    IntegrationRecord,
    parse_lease,
    read_record,
)
from ralph.pipeline.auto_integrate_record_store import apply_record_transition

if TYPE_CHECKING:
    from ralph.pipeline._integration_ladder_seams import (
        EnvironmentProbe,
        FileQuarantine,
        GitPlumbing,
    )
    from ralph.pipeline.integration_actions import Clock
    from ralph.pipeline.integration_attempt_ledger import AttemptLedger


def _operation_root_for(record: IntegrationRecord, default_root: Path) -> Path:
    if (
        record.operation_kind == "target_reconcile"
        and record.owning_worktree
        and DEFAULT_FILE_BACKEND.exists(Path(record.owning_worktree))
    ):
        return Path(record.owning_worktree)
    return default_root


def _settle_spent_budget(
    root: Path,
    record: IntegrationRecord,
    *,
    ledger: AttemptLedger | None = None,
    git: GitPlumbing | None = None,
    fs: FileQuarantine | None = None,
    env: EnvironmentProbe | None = None,
    clock: Clock | None = None,
) -> tuple[IntegrationRecord, bool]:
    if record.merge_commit_repair_attempts < DIAGNOSTIC_REPAIR_ATTEMPT_CAP:
        return record, False
    _fingerprint, held_at = parse_lease(record.diagnostic_ownership)
    is_expired_lease = held_at is None or (time.time() - held_at) > EVIDENCE_LEASE_SECONDS
    is_old_flagged = not record.diagnostic_evidence and not record.diagnostic_ownership
    if is_expired_lease or is_old_flagged or is_owner_dead(record):
        try:
            apply_record_transition(
                root,
                RELEASE_DIAGNOSTIC_EVIDENCE,
                diagnostic_evidence=(),
                diagnostic_ownership=None,
                diagnostic_owner_pid=None,
                diagnostic_owner_boot_id=None,
                diagnostic_intervention_required=False,
                repair_cap_reason=record.repair_cap_reason,
            )
            refreshed = read_record(root)
            if refreshed is not None:
                record = refreshed
        except Exception:
            pass
    if record.merge_commit_repair_attempts < DIAGNOSTIC_REPAIR_ATTEMPT_CAP:
        return record, False
    try:
        escalate_diagnostic_budget_to_ladder(
            root,
            record,
            target=record.target,
            ledger=ledger,
            git=git,
            fs=fs,
            env=env,
            clock=clock,
        )
    except Exception:
        terminal_emitter.critical(
            "Integration diagnostic budget spent but ladder escalation raised; "
            "the durable ledger keeps the climb and the next seam resumes it",
        )
    return record, True


def _completion_proof_applies(record: IntegrationRecord) -> bool:
    kind = classify_git_operation(record)
    if kind in (GitOperationKind.AWAITING_CONTINUE, GitOperationKind.AWAITING_MERGE_COMMIT):
        return True
    return record.is_integrated
