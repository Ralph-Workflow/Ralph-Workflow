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

from pydantic import ConfigDict

from ralph.pydantic_compat import RalphBaseModel

AUTO_INTEGRATE_RECORD_FILENAME = "auto_integrate_in_progress.json"


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
            the target and only the fast-forward remains.
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

    phase: str
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
    crashed prior run never wedges the recovery preamble.
    """
    record_file = record_path(workspace_root)
    if not record_file.exists():
        return None
    try:
        raw = record_file.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data_raw: object = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data_raw, dict):
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
    "IntegrationRecord",
    "clear_record",
    "read_record",
    "record_path",
    "write_record",
]
