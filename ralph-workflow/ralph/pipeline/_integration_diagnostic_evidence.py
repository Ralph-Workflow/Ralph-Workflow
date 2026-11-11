
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


_OPERATION_EVIDENCE_COLLECTOR: list[OperationEvidenceCollector] = [
    SubprocessOperationEvidenceCollector()
]

EVIDENCE_LEASE_SECONDS: float = 1800.0


def set_operation_evidence_collector(
    collector: OperationEvidenceCollector | None,
) -> None:
    _OPERATION_EVIDENCE_COLLECTOR[0] = (
        collector if collector is not None else SubprocessOperationEvidenceCollector()
    )


def operation_evidence_collector() -> OperationEvidenceCollector:
    return _OPERATION_EVIDENCE_COLLECTOR[0]


def diagnostic_evidence_verdict(
    root: Path,
    record: IntegrationRecord,
    collector: OperationEvidenceCollector | None = None,
) -> EvidenceVerdict:
    del collector
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


def snapshot_has_unreadable(
    root: Path,
    record: IntegrationRecord,
    collector: OperationEvidenceCollector | None = None,
) -> bool:
    try:
        col = collector if collector is not None else operation_evidence_collector()
        snapshot = col.snapshot(root, record)
    except Exception:
        return True
    return any(fact.value == _UNREADABLE for fact in snapshot)


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
