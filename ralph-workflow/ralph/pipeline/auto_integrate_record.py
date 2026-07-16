"""Durable crash-record model + I/O for :mod:`ralph.pipeline.auto_integrate`.

Houses the :class:`IntegrationRecord` pydantic model and the
atomically-written record I/O helpers so the main
:mod:`ralph.pipeline.auto_integrate` module stays under the
repo-structure ``_MAX_FILE_LINES`` cap. The four I/O helpers
(``record_path``, ``write_record``, ``read_record``,
``clear_record``) and the record model form a coherent unit --
the phased crash-record file lifecycle -- and have no callers
outside :mod:`ralph.pipeline.auto_integrate`.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict

from ralph.pydantic_compat import RalphBaseModel

AUTO_INTEGRATE_RECORD_FILENAME = "auto_integrate_in_progress.json"

#: Phases the auto-integrate recovery preamble understands.
#: ``integrating`` while the rebase/merge is in flight; ``integrated``
#: once the feature branch fully contains the target and only the
#: fast-forward remains. Any other on-disk value is treated as
#: corrupt by :func:`read_record` -- a malformed record must never
#: be acted on as if it were a known phase.
IntegrationPhase = Literal["integrating", "integrated"]

#: Closed set of valid phase values, used by :func:`read_record` to
#: reject corrupt on-disk records that pass pydantic coercion but
#: carry a phase outside the known protocol (e.g. a value left
#: behind by an older or partially-applied write).
_VALID_PHASES: frozenset[str] = frozenset({"integrating", "integrated"})


class IntegrationRecord(RalphBaseModel):
    """Durable phased record of an in-progress auto-integration.

    Persisted to ``<workspace_scope.root>/.agent/auto_integrate_in_progress.json``
    via :func:`write_record` (atomic temp + ``os.replace``) so a
    SIGKILL mid-write leaves the previous record intact and a
    recovery preamble on resume can decide whether to land the
    fast-forward (phase='integrated') or restore the feature branch
    to its pre-integration state (phase='integrating').

    Attributes:
        phase: ``'integrating'`` while the rebase/merge is in flight;
            ``'integrated'`` once the feature branch fully contains
            the target and only the fast-forward remains. Restricted
            to those two values by the :data:`IntegrationPhase`
            Literal so an on-disk record carrying a stray value is
            rejected by :func:`read_record` as corrupt instead of
            being acted on.
        target: The integration target branch name.
        pre_feature_sha: The feature branch HEAD SHA captured BEFORE
            any rebase/merge; used to restore on a crash that
            interrupts the rebase.
        pre_target_sha: The target branch SHA captured BEFORE the
            fast-forward attempt; the observed ``<oldvalue>`` for
            the atomic compare-and-swap.
        integrated_feature_sha: The feature branch HEAD SHA captured
            AFTER the rebase/merge succeeded. Present only when
            phase='integrated'.
    """

    model_config = ConfigDict(frozen=True)

    phase: IntegrationPhase
    target: str
    pre_feature_sha: str
    pre_target_sha: str | None
    integrated_feature_sha: str | None = None


def record_path(workspace_root: Path) -> Path:
    """Return the durable crash-record path anchored to ``workspace_root``."""
    return workspace_root / ".agent" / AUTO_INTEGRATE_RECORD_FILENAME


def write_record(workspace_root: Path, record: IntegrationRecord) -> None:
    """Atomically write ``record`` to the durable record path.

    The atomic-write pattern (temp file in the same directory + ``os.replace``)
    mirrors the existing ``ralph.git.operations._atomic_append_text``
    contract: a crash mid-write never leaves a half-written record on
    disk. If ``os.replace`` fails, the staging file is removed before
    re-raising.
    """
    record_file = record_path(workspace_root)
    record_file.parent.mkdir(parents=True, exist_ok=True)
    payload = record.model_dump_json().encode("utf-8")
    fd, staging_path = tempfile.mkstemp(
        prefix=record_file.name + ".staging.", dir=str(record_file.parent)
    )
    try:
        with os.fdopen(fd, "wb") as staging:
            staging.write(payload)
        Path(staging_path).replace(record_file)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            Path(staging_path).unlink()
        raise


def read_record(workspace_root: Path) -> IntegrationRecord | None:
    """Return the durable record or ``None`` when absent / corrupt.

    A corrupt record is treated as absent so a partial write from a
    crashed prior run never wedges the recovery preamble. Corrupt
    here means: missing file, unreadable file, invalid JSON,
    non-object payload, schema mismatch, OR an on-disk ``phase``
    outside the :data:`IntegrationPhase` Literal (e.g. a stray
    value left behind by an older partially-applied write). A
    record with a stray phase must never be acted on as if it were
    a known phase -- the recovery path would otherwise run the
    ``integrated`` fast-forward continuation on a record that is
    neither integrating nor integrated.
    """
    record_file = record_path(workspace_root)
    if not record_file.exists():
        return None
    raw = _read_record_raw(record_file)
    if raw is None:
        return None
    return _parse_record_payload(raw)


def _read_record_raw(record_file: Path) -> dict[str, object] | None:
    """Read and parse the record file; return the parsed dict or None.

    Splits out the read+JSON-parse steps from :func:`read_record` so
    each helper stays under the ruff PLR0911 return-statement cap.
    Returns ``None`` for any read or parse failure (treated as
    corrupt / absent by the recovery preamble).
    """
    try:
        raw_text = record_file.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        parsed: object = json.loads(raw_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _parse_record_payload(data_raw: dict[str, object]) -> IntegrationRecord | None:
    """Validate the parsed record payload against the model contract.

    Splits the schema-level validation out of :func:`read_record` so
    the I/O helper stays under the ruff PLR0911 return-statement
    cap. Returns ``None`` for any rejection (corrupt file).
    """
    # Reject a stray ``phase`` value before pydantic validation
    # would coerce it (pydantic honors ``field: str`` for any
    # string). The Literal-typed ``phase`` field already rejects
    # unknown values during ``model_validate`` below, but an
    # explicit pre-check lets us keep the rejection logic here in
    # the I/O helper that owns the corrupt-record contract.
    phase_value = data_raw.get("phase")
    if not isinstance(phase_value, str) or phase_value not in _VALID_PHASES:
        return None
    try:
        return IntegrationRecord.model_validate(data_raw)
    except Exception:
        return None


def clear_record(workspace_root: Path) -> None:
    """Unlink the durable record; missing-ok."""
    record_file = record_path(workspace_root)
    try:
        record_file.unlink()
    except FileNotFoundError:
        return


__all__ = [
    "AUTO_INTEGRATE_RECORD_FILENAME",
    "IntegrationPhase",
    "IntegrationRecord",
    "clear_record",
    "read_record",
    "record_path",
    "write_record",
]
