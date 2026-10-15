"""In-memory fake for operation-scoped diagnostic evidence collection.

Defines :class:`InMemoryOperationEvidenceCollector`, an in-memory implementation
used by tests to avoid real subprocess or Git calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.operation_fact import (
    _KIND_MERGE_OBJECT_RESOLVABLE,
    _KIND_REBASE_OBJECT_RESOLVABLE,
    OperationFact,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.auto_integrate_record import IntegrationRecord


class InMemoryOperationEvidenceCollector:
    """Test fake that returns caller-supplied facts and reachability observations."""

    def __init__(
        self,
        facts: tuple[OperationFact, ...] = (),
        reachability: dict[str, bool] | None = None,
    ) -> None:
        facts_list = list(facts)
        if reachability:
            if "MERGE_HEAD" in reachability:
                facts_list.append(
                    OperationFact(
                        kind=_KIND_MERGE_OBJECT_RESOLVABLE,
                        value=str(reachability["MERGE_HEAD"]),
                    )
                )
            if "REBASE_HEAD" in reachability:
                facts_list.append(
                    OperationFact(
                        kind=_KIND_REBASE_OBJECT_RESOLVABLE,
                        value=str(reachability["REBASE_HEAD"]),
                    )
                )
        self._facts = tuple(facts_list)
        self._reachability = reachability or {}

    def snapshot(self, root: Path, record: IntegrationRecord) -> tuple[OperationFact, ...]:
        return self._facts

    def verify(
        self,
        root: Path,
        record: IntegrationRecord,
        observed: tuple[OperationFact, ...],
    ) -> bool:
        for fact in self._facts:
            if fact.kind in (
                _KIND_MERGE_OBJECT_RESOLVABLE,
                _KIND_REBASE_OBJECT_RESOLVABLE,
            ) and fact.value in ("False", "false"):
                return False
        for fact in observed:
            if fact.kind in (
                _KIND_MERGE_OBJECT_RESOLVABLE,
                _KIND_REBASE_OBJECT_RESOLVABLE,
            ) and fact.value in ("False", "false"):
                return False
        return self._facts == observed


__all__ = ["InMemoryOperationEvidenceCollector"]
