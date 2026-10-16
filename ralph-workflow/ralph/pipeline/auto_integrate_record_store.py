"""Durable store and workspace boundary for IntegrationRecord transitions.

Split from :mod:`ralph.pipeline.auto_integrate_record` so neither
module carries two top-level classes (repo-structure policy).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    RecordReadOutcome,
    RecordReadOutcomeDisposition,
    RecordUnreadableError,
    clear_record,
    read_record,
    read_record_outcome,
    record_path,
    write_record,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.auto_integrate_transition import Transition


class IntegrationRecordStore:
    """Injected durable store and workspace boundary for IntegrationRecord transitions.

    Guarantees that every Transition is durable and atomic through the injected
    backend / workspace. Crash before or after a transition is idempotent.
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        backend: FileBackend = DEFAULT_FILE_BACKEND,
    ) -> None:
        self._workspace_root = workspace_root
        self._backend = backend

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    @property
    def backend(self) -> FileBackend:
        return self._backend

    @property
    def record_path(self) -> Path:
        return record_path(self._workspace_root)

    def read(self) -> IntegrationRecord | None:
        """Read the authoritative record from this store."""
        return read_record(self._workspace_root, backend=self._backend)

    def write(self, record: IntegrationRecord) -> None:
        """Atomically persist record to this store."""
        write_record(self._workspace_root, record, backend=self._backend)

    def clear(self) -> None:
        """Clear the record file from this store."""
        clear_record(self._workspace_root, backend=self._backend)

    def read_outcome(self) -> RecordReadOutcome:
        """Read the authoritative record as a CLOSED typed outcome."""
        return read_record_outcome(self._workspace_root, backend=self._backend)

    def transition(
        self,
        transition: Transition,
        **fields: object,
    ) -> IntegrationRecord:
        """Apply a Transition atomically and durably to the stored record.

        Consumes the typed read outcome: when the durable evidence is
        UNREADABLE, EVERY mutation is refused with a typed
        :class:`RecordUnreadableError` -- the corrupt bytes are the
        only proof an integration was interrupted, so overwriting
        them with a fresh START_INTEGRATING record would erase the
        evidence. ABSENT keeps the existing START_INTEGRATING /
        CLEAR semantics.

        Loads the authoritative record from this store, applies the transition
        via :meth:`IntegrationRecord.with_transition`, and writes the new record
        atomically.

        Idempotence: If called with identical transition and fields when the
        transition has already been applied, the write is a no-op (bytes unchanged),
        ensuring crash-recovery replay before or after the transition is safe.
        """
        outcome = self.read_outcome()
        if outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE:
            raise RecordUnreadableError(
                f"refusing transition {transition.name!r}: unreadable record "
                f"evidence at {self.record_path} "
                f"(reason: {outcome.unreadable_reason})"
            )
        current = outcome.record
        if current is None:
            if transition.name == "START_INTEGRATING":
                base = IntegrationRecord(
                    phase="integrating",
                    target="",
                    pre_feature_sha="",
                    pre_target_sha=None,
                )
                initial_record = base.with_transition(transition, **fields)
                self.write(initial_record)
                return initial_record
            elif transition.name == "CLEAR":
                return IntegrationRecord(
                    phase="integrating",
                    target="",
                    pre_feature_sha="",
                    pre_target_sha=None,
                )
            raise ValueError(
                f"Cannot apply transition {transition.name!r}: "
                f"no active record found at {self.record_path}"
            )
        # Idempotent replay after crash for PROMOTE_TO_INTEGRATED
        if transition.name == "PROMOTE_TO_INTEGRATED" and current.phase == "integrated":
            expected_sha = fields.get("integrated_feature_sha")
            if expected_sha is None or current.integrated_feature_sha == expected_sha:
                return current
        updated = current.with_transition(transition, **fields)
        self.write(updated)
        return updated


def apply_record_transition(
    workspace_root: Path,
    transition: Transition,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    **fields: object,
) -> IntegrationRecord:
    """Apply a transition and persist it atomically to the durable record."""
    store = IntegrationRecordStore(workspace_root, backend=backend)
    return store.transition(transition, **fields)


__all__ = [
    "IntegrationRecordStore",
    "RecordUnreadableError",
    "apply_record_transition",
]
