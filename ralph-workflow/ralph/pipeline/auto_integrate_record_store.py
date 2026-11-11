
from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    RecordBusyError,
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
        return read_record(self._workspace_root, backend=self._backend)

    def write(self, record: IntegrationRecord) -> None:
        write_record(self._workspace_root, record, backend=self._backend)

    def clear(self) -> None:
        clear_record(self._workspace_root, backend=self._backend)

    def read_outcome(self) -> RecordReadOutcome:
        return read_record_outcome(self._workspace_root, backend=self._backend)

    def transition(
        self,
        transition: Transition,
        **fields: object,
    ) -> IntegrationRecord:
        outcome = self.read_outcome()
        if outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE:
            raise RecordUnreadableError(
                f"refusing transition {transition.name!r}: unreadable record "
                f"evidence at {self.record_path} "
                f"(reason: {outcome.unreadable_reason})"
            )
        if outcome.disposition is RecordReadOutcomeDisposition.BUSY:
            raise RecordBusyError(
                f"refusing transition {transition.name!r}: transient record "
                f"lock contention at {self.record_path}; bounded retry with a "
                "persisted deadline owns the backoff"
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
    store = IntegrationRecordStore(workspace_root, backend=backend)
    return store.transition(transition, **fields)


__all__ = [
    "IntegrationRecordStore",
    "RecordBusyError",
    "RecordUnreadableError",
    "apply_record_transition",
]
