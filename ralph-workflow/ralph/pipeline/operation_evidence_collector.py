"""Protocol for operation-scoped diagnostic evidence collection.

Defines :class:`OperationEvidenceCollector`, the contract governing
readers of Git state for diagnostic evidence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ralph.pipeline.operation_evidence_verdict import EvidenceVerdict

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.operation_fact import OperationFact


class OperationEvidenceCollector(Protocol):
    """The only component permitted to read Git state for diagnostic evidence.

    Implementations MUST scope every read to the operation worktree
    passed in ``root`` (which the diagnostic path derives from
    ``record.owning_worktree`` for ``target_reconcile`` and from
    ``workspace_scope.root`` otherwise) and MUST NOT read repository
    state owned by an unrelated worktree or branch. Implementations
    MUST also never emit a value of ``None`` -- an unreadable
    observation is encoded as the string ``"unreadable"`` so the
    durable record's equality contract stays stable.
    """

    def snapshot(self, root: Path, record: IntegrationRecord) -> tuple[OperationFact, ...]:
        """Return the operation-scoped facts for ``record``."""
        ...

    def evaluate(
        self,
        root: Path,
        record: IntegrationRecord,
        observed: tuple[OperationFact, ...],
    ) -> EvidenceVerdict:
        """Evaluate observed evidence against the live snapshot and return an :class:`EvidenceVerdict`."""
        ...

    def verify(
        self,
        root: Path,
        record: IntegrationRecord,
        observed: tuple[OperationFact, ...],
    ) -> bool:
        """Return ``True`` iff the live snapshot still matches ``observed``."""
        ...


__all__ = ["EvidenceVerdict", "OperationEvidenceCollector"]
