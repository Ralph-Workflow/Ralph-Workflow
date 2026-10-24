"""Diagnostic evidence lease: the trust verdict for a held diagnostic hold.

The diagnostic path holds a durable lease on the integration record
while it inspects the operation. This module owns the two things that
decide whether that lease is still trustworthy:

* the EVIDENCE COLLECTOR seam (which collector the diagnostic path
  reads the operation evidence with), and
* the LEASE VERDICT (whether the held lease may still authorise a
  release).

The verdict is lease-based, not snapshot-based: the lease in
``diagnostic_ownership`` (``<fingerprint>:<held_at>``) is the sole
release authority. Comparing a stored evidence snapshot to live Git
state is exactly the comparison that made an unrelated branch
advancement in another worktree look like evidence corruption.

The four-condition Git proof that consumes this verdict lives in
:mod:`ralph.pipeline._integration_diagnostic_proof`.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from ralph.pipeline._auto_integrate_record_restamp import is_owner_dead
from ralph.pipeline.auto_integrate_record import parse_lease
from ralph.pipeline.operation_evidence import (
    EvidenceVerdict,
    OperationEvidenceCollector,
    SubprocessOperationEvidenceCollector,
    identity_fingerprint,
)
from ralph.pipeline.operation_fact import _UNREADABLE

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.auto_integrate_record import IntegrationRecord

__all__ = [
    "EVIDENCE_LEASE_SECONDS",
    "diagnostic_evidence_unchanged",
    "diagnostic_evidence_verdict",
    "evaluate_held_diagnostic_evidence",
    "operation_evidence_collector",
    "release_verified_diagnostic",
    "set_operation_evidence_collector",
    "snapshot_has_unreadable",
]


# Default collector used by the diagnostic path. Tests inject
# ``InMemoryOperationEvidenceCollector`` (or a custom subclass) to
# exercise the diagnostic without depending on the host's ``git``
# binary or filesystem layout.
_OPERATION_EVIDENCE_COLLECTOR: list[OperationEvidenceCollector] = [
    SubprocessOperationEvidenceCollector()
]

#: Default lease lifetime in seconds. The diagnostic HOLD step writes
#: ``diagnostic_ownership = f"{identity_fingerprint(record)}:{time.time()}"``.
#: A subsequent run whose elapsed time exceeds this value treats the
#: lease as expired and auto-releases (fail-open: ``UNKNOWN`` verdict).
#: 30 minutes accommodates the longest legitimate agent invocation
#: while ensuring a process killed between HOLD and RELEASE becomes
#: auto-recoverable on the next run.
EVIDENCE_LEASE_SECONDS: float = 1800.0


def set_operation_evidence_collector(
    collector: OperationEvidenceCollector | None,
) -> None:
    """Inject (or clear) the diagnostic path's evidence collector.

    Used by tests that need an in-memory fake. ``None`` resets the
    default subprocess collector.
    """
    _OPERATION_EVIDENCE_COLLECTOR[0] = (
        collector if collector is not None else SubprocessOperationEvidenceCollector()
    )


def operation_evidence_collector() -> OperationEvidenceCollector:
    """Return the diagnostic path's currently-configured collector."""
    return _OPERATION_EVIDENCE_COLLECTOR[0]


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
    del collector  # the lease is the sole release authority
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


def snapshot_has_unreadable(root: Path, record: IntegrationRecord) -> bool:
    """Run the snapshot and report whether any fact is UNREADABLE.

    The snapshot is preserved public API (tests in
    :mod:`tests.test_operation_evidence`); the diagnostic path no
    longer compares the snapshot to held evidence but still consults
    it to refuse release when live state cannot be observed. A
    UNREADABLE fact is the diagnostic equivalent of an indeterminate
    merge state and must block the atomic transition proof.
    """
    try:
        col = operation_evidence_collector()
        snapshot = col.snapshot(root, record)
    except Exception:
        return True
    return any(fact.value == _UNREADABLE for fact in snapshot)


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
