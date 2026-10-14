"""In-memory fake for operation-scoped diagnostic evidence collection.

Defines :class:`InMemoryOperationEvidenceCollector`, an in-memory implementation
used by tests to avoid real subprocess or Git calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.operation_fact import OperationFact


class InMemoryOperationEvidenceCollector:
    """Test fake that returns caller-supplied facts verbatim.

    Tests use this collector to simulate a fingerprint that has (or
    has not) drifted, without depending on the host's ``git`` binary
    or filesystem layout. The fake never falls back to a subprocess
    call, so it is safe to use in tests that explicitly forbid real
    subprocess I/O.
    """

    def __init__(self, facts: tuple[OperationFact, ...] = ()) -> None:
        self._facts = tuple(facts)

    def snapshot(self, root: Path, record: IntegrationRecord) -> tuple[OperationFact, ...]:
        return self._facts

    def verify(
        self,
        root: Path,
        record: IntegrationRecord,
        observed: tuple[OperationFact, ...],
    ) -> bool:
        return self._facts == observed


__all__ = ["InMemoryOperationEvidenceCollector"]
