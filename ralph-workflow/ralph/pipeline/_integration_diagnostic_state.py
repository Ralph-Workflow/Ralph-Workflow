"""Shared verdict rules and policy limits of the integration diagnostic path.

The diagnostic path is the single authority that decides whether the retained
integration evidence is still trustworthy. This module owns the two pieces
every diagnostic site needs: the policy limits (the repair-attempt cap and the
lease lifetime) and the lease/verdict rules that read them.

Split out of ``ralph.pipeline._integration_diagnostic`` to stay within the
repo-structure line limits; the public surface is unchanged and re-exported
from there.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.pipeline._auto_integrate_record_restamp import is_owner_dead
from ralph.pipeline._git_operation_authority import GitOperationKind, classify_git_operation
from ralph.pipeline.auto_integrate_record import parse_lease
from ralph.pipeline.operation_evidence import (
    EvidenceVerdict,
    SubprocessOperationEvidenceCollector,
    identity_fingerprint,
)

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.operation_evidence import OperationEvidenceCollector

#: Default repair-attempt cap. The diagnostic path rotates inspection
#: agents on every handoff, but the rotation must terminate: an
#: over-cap handoff emits the ``RAISE_DIAGNOSTIC_INTERVENTION``
#: transition and stops calling agents. Operators can raise the cap
#: via ``conflict_resolution.diagnostic_repair_attempt_cap`` in
#: policy configuration; the default is intentionally low so the
#: production loop is broken on the very first incident.
DIAGNOSTIC_REPAIR_ATTEMPT_CAP: int = 5

#: Default lease lifetime in seconds. The diagnostic HOLD step writes
#: ``diagnostic_ownership = f"{identity_fingerprint(record)}:{time.time()}"``.
#: A subsequent run whose elapsed time exceeds this value treats the
#: lease as expired and auto-releases (fail-open: ``UNKNOWN`` verdict).
#: 30 minutes accommodates the longest legitimate agent invocation
#: while ensuring a process killed between HOLD and RELEASE becomes
#: auto-recoverable on the next run.
EVIDENCE_LEASE_SECONDS: float = 1800.0

#: The single mutable holder of the diagnostic path's evidence collector.
#: The public write seam (:func:`set_operation_evidence_collector`) lives in
#: ``ralph.pipeline._integration_diagnostic``; every reader resolves the
#: current collector through :func:`operation_evidence_collector` below.
_OPERATION_EVIDENCE_COLLECTOR: list[OperationEvidenceCollector] = [
    SubprocessOperationEvidenceCollector()
]


def operation_evidence_collector() -> OperationEvidenceCollector:
    """Return the diagnostic path's currently-configured collector."""
    return _OPERATION_EVIDENCE_COLLECTOR[0]


def _operation_root_for(record: IntegrationRecord, default_root: Path) -> Path:
    """Single source of truth for the diagnostic's operation root.

    The diagnostic only switches to ``record.owning_worktree`` when
    the operation is a ``target_reconcile`` AND the path still
    exists on disk; otherwise it falls back to the caller-supplied
    ``default_root``. Four call sites used to implement this gate
    with subtly different predicates; this helper is the canonical
    rule every site must consult.
    """
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
    """Evaluate whether the held diagnostic lease is still trustworthy.

    The diagnostic no longer compares the evidence snapshot. The
    lease in ``diagnostic_ownership`` (``<fingerprint>:<held_at>``)
    is the sole release authority:

    * Empty ``diagnostic_evidence`` and no ``diagnostic_ownership`` --
      no live hold; the record is already released. Returns
      ``UNCHANGED`` so the caller can proceed to recovery.
    * Non-empty ``diagnostic_evidence`` and no ``diagnostic_ownership``
      (missing lease) -- returns ``UNKNOWN``; the caller
      auto-releases fail-open and re-derives from Git.
    * Non-empty unparseable ``diagnostic_ownership``
      (malformed lease) -- returns ``UNKNOWN``; the caller
      auto-releases fail-open and re-derives from Git.
    * Fingerprint mismatch (the durable identity was rewritten by
      a different worktree) -- returns ``CHANGED`` (valid old lease
      bytes preserved on disk).
    * Lease parseable and unexpired -- returns ``UNCHANGED``.
    * Lease parseable and expired -- returns ``UNKNOWN``; the caller
      auto-releases (fail-open).

    Returns:
        EvidenceVerdict.UNCHANGED: the lease is valid; release.
        EvidenceVerdict.CHANGED: the durable identity was rewritten.
        EvidenceVerdict.UNKNOWN: the lease is expired, missing, or unparseable.
    """
    if not record.diagnostic_evidence and not record.diagnostic_ownership:
        return EvidenceVerdict.UNCHANGED
    if is_owner_dead(record) or record.diagnostic_ownership is None:
        return EvidenceVerdict.UNKNOWN
    fingerprint, held_at = parse_lease(record.diagnostic_ownership)
    is_expired = held_at is not None and (time.time() - held_at) > EVIDENCE_LEASE_SECONDS
    if is_expired or fingerprint is None:
        return EvidenceVerdict.UNKNOWN
    if fingerprint != identity_fingerprint(record):
        # Valid old lease bytes with differing fingerprint: preserve as CHANGED.
        return EvidenceVerdict.CHANGED
    return EvidenceVerdict.UNCHANGED


evaluate_held_diagnostic_evidence = diagnostic_evidence_verdict


def diagnostic_evidence_unchanged(
    root: Path,
    record: IntegrationRecord,
    collector: OperationEvidenceCollector | None = None,
) -> bool:
    """Return True iff diagnostic evidence evaluates to UNCHANGED."""
    return (
        diagnostic_evidence_verdict(root, record, collector=collector) == EvidenceVerdict.UNCHANGED
    )


def release_verified_diagnostic(
    root: Path,
    record: IntegrationRecord,
    collector: OperationEvidenceCollector | None = None,
) -> EvidenceVerdict:
    """Evaluate diagnostic evidence without mutating durable record state.

    Returns an :class:`EvidenceVerdict`. The caller owns the transition
    and durable record write when the verdict is ``UNCHANGED``.
    """
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
