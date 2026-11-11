
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ralph.pipeline.operation_evidence_verdict import EvidenceVerdict

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.operation_fact import OperationFact


class OperationEvidenceCollector(Protocol):

    def snapshot(self, root: Path, record: IntegrationRecord) -> tuple[OperationFact, ...]:
        ...

    def evaluate(
        self,
        root: Path,
        record: IntegrationRecord,
        observed: tuple[OperationFact, ...],
    ) -> EvidenceVerdict:
        ...

    def verify(
        self,
        root: Path,
        record: IntegrationRecord,
        observed: tuple[OperationFact, ...],
    ) -> bool:
        ...


__all__ = ["EvidenceVerdict", "OperationEvidenceCollector"]
