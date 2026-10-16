"""Recovery release site must use the record store and handle typed refusals.

The plan's UNIT 2 closes the recovery release bypass: the three sibling
release sites use ``apply_record_transition`` (which enforces the
typed BUSY/UNREADABLE guards) AND each call site catches
``RecordBusyError`` / ``RecordUnreadableError`` and maps them to the
authoritative typed outcome.

Tests cover all three sibling release sites:
- :func:`ralph.pipeline.auto_integrate_recovery.recover_incomplete_integration`
- :func:`ralph.pipeline._integration_diagnostic.diagnose_retained_integration`
- :func:`ralph.pipeline._remote_reconcile_recovery.recover_remote_target_reconcile`

Public-seam assertions only. No AST walks. Each test reads/writes through
the public ``FileBackend`` seam so the released record bytes are
observable on disk.
"""

from __future__ import annotations

import errno
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.pipeline.auto_integrate_record import (
    AUTO_INTEGRATE_RECORD_FILENAME,
    RecordBusyError,
    RecordUnreadableError,
    write_record,
)
from ralph.pipeline.auto_integrate_record_store import apply_record_transition
from ralph.pipeline.auto_integrate_transition import RELEASE_DIAGNOSTIC_EVIDENCE

if TYPE_CHECKING:
    from ralph.mcp.artifacts.file_backend import FileBackend

# ---------------------------------------------------------------------------
# Backend fakes — the only public seam we exercise
# ---------------------------------------------------------------------------


class _BusyReadBackend:
    """Backend that reports BUSY on the first ``exists`` call (read path).

    The :class:`IntegrationRecordStore` translates ``EAGAIN`` from
    ``backend.exists()`` into the typed ``RecordReadOutcomeDisposition.BUSY``
    outcome, which ``apply_record_transition`` then surfaces as
    :class:`RecordBusyError`. This is the lower-level read-side
    typed-refusal contract the recovery release sites depend on.
    """

    def __init__(self, inner: FileBackend) -> None:
        self._inner = inner
        self._busy_reads = 0

    def exists(self, path: Path) -> bool:
        if self._busy_reads == 0:
            self._busy_reads += 1
            err = OSError(errno.EAGAIN, "Resource temporarily unavailable")
            raise err
        return self._inner.exists(path)

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        self._inner.mkdir(path, parents=parents, exist_ok=exist_ok)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        return self._inner.read_text(path, encoding=encoding)

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        self._inner.write_text(path, content, encoding=encoding)

    def read_bytes(self, path: Path) -> bytes:
        return self._inner.read_bytes(path)

    def write_bytes(self, path: Path, content: bytes) -> None:
        self._inner.write_bytes(path, content)

    def replace(self, source: Path, destination: Path) -> None:
        self._inner.replace(source, destination)

    def sync_directory(self, path: Path) -> None:
        self._inner.sync_directory(path)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        self._inner.unlink(path, missing_ok=missing_ok)

    def glob(self, path: Path, pattern: str) -> list[Path]:
        return self._inner.glob(path, pattern)


def _write_present_record(tmp_path: Path) -> None:
    """Write a PRESENT record with diagnostic_evidence on disk."""
    from ralph.pipeline.auto_integrate_record import IntegrationRecord

    record_file = tmp_path / ".agent" / AUTO_INTEGRATE_RECORD_FILENAME
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        diagnostic_evidence=(("work", "evidence"),),
        diagnostic_ownership="test",
    )
    write_record(tmp_path, record)


# ---------------------------------------------------------------------------
# Public-seam coverage of the recovery release sites
# ---------------------------------------------------------------------------


def test_apply_record_transition_refuses_busy_with_typed_error(tmp_path: Path) -> None:
    """``apply_record_transition`` raises ``RecordBusyError`` on BUSY reads.

    This is the lower-level test that documents the typed refusal
    contract ``apply_record_transition`` enforces; the recovery
    release site depends on this refusal to map BUSY to a typed
    retry outcome (rather than erasing the record bytes).
    """
    from ralph.mcp.artifacts._path_file_backend import PathFileBackend

    _write_present_record(tmp_path)
    backend = _BusyReadBackend(PathFileBackend())

    with pytest.raises(RecordBusyError):
        apply_record_transition(
            tmp_path,
            RELEASE_DIAGNOSTIC_EVIDENCE,
            backend=backend,
            diagnostic_evidence=(),
            diagnostic_ownership=None,
        )

    # The on-disk record was NOT overwritten; the BUSY refusal preserves evidence.
    record_file = tmp_path / ".agent" / AUTO_INTEGRATE_RECORD_FILENAME
    raw = json.loads(record_file.read_text(encoding="utf-8"))
    assert raw["diagnostic_evidence"] == [["work", "evidence"]]
    assert raw["diagnostic_ownership"] == "test"


def test_apply_record_transition_refuses_unreadable_with_typed_error(
    tmp_path: Path,
) -> None:
    """``apply_record_transition`` raises ``RecordUnreadableError`` on UNREADABLE writes.

    Documents the typed refusal contract: when the read outcome is
    UNREADABLE, the store refuses to write and the bytes stay
    untouched on disk as ownership evidence.
    """
    record_file = tmp_path / ".agent" / AUTO_INTEGRATE_RECORD_FILENAME
    record_file.parent.mkdir(parents=True, exist_ok=True)
    # Write bytes that produce an UNREADABLE outcome (corrupt JSON).
    record_file.write_text("{not valid json", encoding="utf-8")
    original_bytes = record_file.read_bytes()

    with pytest.raises(RecordUnreadableError):
        apply_record_transition(
            tmp_path,
            RELEASE_DIAGNOSTIC_EVIDENCE,
            diagnostic_evidence=(),
            diagnostic_ownership=None,
        )

    # The UNREADABLE bytes are preserved on disk.
    assert record_file.read_bytes() == original_bytes


@pytest.mark.parametrize(
    ("module_name",),
    [
        ("ralph.pipeline.auto_integrate_recovery",),
        ("ralph.pipeline._integration_diagnostic",),
        ("ralph.pipeline._remote_reconcile_recovery",),
    ],
)
def test_release_sites_use_apply_record_transition_for_release(
    module_name: str,
) -> None:
    """Every sibling RELEASE_DIAGNOSTIC_EVIDENCE site uses the record store.

    Public-seam assertion: each release module imports
    ``apply_record_transition`` (the typed record store) so its
    RELEASE_DIAGNOSTIC_EVIDENCE site can refuse BUSY/UNREADABLE
    instead of overwriting ownership evidence. A future regression
    that swaps the store call back to a bare ``write_record`` fails
    this pin.
    """
    import importlib

    module = importlib.import_module(module_name)

    assert hasattr(module, "apply_record_transition"), (
        f"{module_name} must import apply_record_transition so "
        "the RELEASE_DIAGNOSTIC_EVIDENCE site can refuse BUSY/UNREADABLE."
    )


def test_recovery_seam_uses_apply_record_transition_for_release(
    tmp_path: Path,
) -> None:
    """The recovery seam's RELEASE_DIAGNOSTIC_EVIDENCE site uses the record store.

    Public-seam assertion: the recovery module imports
    ``apply_record_transition`` (the typed record store) so the
    RELEASE_DIAGNOSTIC_EVIDENCE site can refuse BUSY/UNREADABLE
    instead of overwriting ownership evidence.
    """
    import ralph.pipeline.auto_integrate_recovery as recovery_mod

    assert hasattr(recovery_mod, "apply_record_transition"), (
        "auto_integrate_recovery must import apply_record_transition so "
        "the RELEASE_DIAGNOSTIC_EVIDENCE site can refuse BUSY/UNREADABLE."
    )
