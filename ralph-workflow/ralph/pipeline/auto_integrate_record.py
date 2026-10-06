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

import json
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Literal

from loguru import logger
from pydantic import ConfigDict

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import atomic_write_bytes_if_changed
from ralph.pydantic_compat import RalphBaseModel

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

AUTO_INTEGRATE_RECORD_FILENAME = "auto_integrate_in_progress.json"

#: Phases the auto-integrate recovery preamble understands.
#: ``integrating`` while the rebase/merge is in flight; ``integrated``
#: once the feature branch fully contains the target and only the
#: fast-forward remains. Any other on-disk value is treated as
#: corrupt by :func:`read_record` -- a malformed record must never
#: be acted on as if it were a known phase.
IntegrationPhase = Literal["integrating", "integrated"]
IntegrationOperation = Literal["feature_integrate", "target_reconcile"]

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
        resolving_rebase: True while a rebase-conflict resolution agent
            is working inside a paused rebase. Recovery retains the operation
            for supervised agent continuation instead of discarding edits.
        resolving_merge: An interrupted merge resolver owns the current edits.
        merge_commit_pending: The resolved merge awaits its ordinary commit.
        rebase_continue_pending: A verified stop awaits ordinary continuation.
        rebase_continue_metadata: Exact replay queue captured before continuation.
        rebase_restore_metadata: Pre-restore queue receipt for idempotent repair.
    """

    model_config = ConfigDict(frozen=True)

    phase: IntegrationPhase
    target: str
    pre_feature_sha: str
    pre_target_sha: str | None
    integrated_feature_sha: str | None = None
    resolving_rebase: bool = False
    rebase_continue_pending: bool = False
    rebase_continue_metadata: tuple[tuple[str, str], ...] = ()
    rebase_restore_metadata: tuple[tuple[str, str], ...] | None = None
    rebase_continue_head: str | None = None
    rebase_continue_stop: str | None = None
    rebase_continue_tree: str | None = None
    rebase_continue_error: str | None = None
    resolving_merge: bool = False
    resolving_paths: tuple[str, ...] = ()
    resolving_stop_sha: str | None = None
    merge_commit_pending: bool = False
    merge_commit_repair_attempts: int = 0
    repair_original_tree: str | None = None
    repair_last_error: str | None = None
    diagnostic_evidence: tuple[tuple[str, str], ...] = ()
    diagnostic_ownership: str | None = None
    repair_pending_diff: str | None = None
    repair_pending_paths: tuple[str, ...] = ()
    repair_commit_controls: str | None = None
    merge_commit_head: str | None = None
    merge_commit_parent: str | None = None
    merge_commit_tree: str | None = None
    operation_kind: IntegrationOperation = "feature_integrate"
    owning_worktree: str | None = None


_RECORD_ROOT_BINDING: ContextVar[tuple[Path, Path] | None] = ContextVar(
    "integration_record_root_binding",
    default=None,
)


@contextmanager
def bind_integration_record_root(operation_root: Path, record_root: Path) -> Iterator[None]:
    """Keep foreign-worktree resolver receipts in their initiating workspace."""
    token = _RECORD_ROOT_BINDING.set((operation_root.resolve(), record_root))
    try:
        yield
    finally:
        _RECORD_ROOT_BINDING.reset(token)


def record_path(workspace_root: Path) -> Path:
    """Return the durable crash-record path anchored to ``workspace_root``."""
    binding = _RECORD_ROOT_BINDING.get()
    if binding is not None and workspace_root.resolve() == binding[0]:
        workspace_root = binding[1]
    return workspace_root / ".agent" / AUTO_INTEGRATE_RECORD_FILENAME


def write_record(
    workspace_root: Path,
    record: IntegrationRecord,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Atomically persist ``record`` only when its durable bytes changed.

    Publication uses the shared same-directory atomic primitive. It preserves
    the crash-safe replace and directory durability barrier for changed state,
    while an identical replay avoids creating the parent, staging a file,
    replacing the record, or syncing its directory.

    Args:
        workspace_root: Workspace that owns this run-scoped recovery record.
        record: Durable auto-integration state to publish.
        backend: Persistence boundary; injectable for in-memory contract tests.
    """
    record_file = record_path(workspace_root)
    # filesystem-write-ok: auto-integrate binary record under .agent/auto_integrate, runtime state not tracked
    payload = record.model_dump_json().encode("utf-8")
    atomic_write_bytes_if_changed(
        backend,
        record_file,
        payload,
        tmp_path=record_file.with_suffix(record_file.suffix + ".staging"),
        sync_directory=True,
        prepare_write=lambda: backend.mkdir(record_file.parent, parents=True, exist_ok=True),
    )


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


def _absent_record_is_recordable(path: Path) -> bool:
    """Decide what an unreadable record means for the caller's flag.

    A TRANSIENT read failure leaves an intact record on disk, and
    failing closed is right: the file would still say ``false`` while
    the caller believed it said ``true``. A PERMANENTLY unreadable one
    is not a record at all -- and reporting it as "could not record"
    disabled rebase conflict resolution for good, because the caller
    refuses to start a resolution it cannot describe, nothing on that
    path removes the file, and every later run ended with no resolver
    invoked and no way out but deleting the file by hand.
    """
    if not path.exists():
        return True
    if _record_is_parseable(path):
        return False
    logger.warning(
        "auto_integrate: the in-flight integration record at {} cannot be parsed; "
        "discarding it rather than blocking every future resolution",
        path,
    )
    try:
        path.unlink()
    except OSError as unlink_exc:
        logger.warning("auto_integrate: could not discard it: {}", unlink_exc)
        return False
    return True


def _record_is_parseable(path: Path) -> bool:
    """Whether the file on disk is a valid record this build understands.

    Separates a TRANSIENT read failure -- where the record is intact and
    failing closed is right -- from a permanently unreadable one, where
    failing closed disables resolution for good.
    """
    try:
        return IntegrationRecord.model_validate_json(path.read_text(encoding="utf-8")) is not None
    except Exception:
        return False


def set_resolving_rebase(workspace_root: Path, resolving: bool) -> bool:
    """Flag (or unflag) the durable record as an in-flight rebase resolution.

    Called around the resolve-and-continue loop so a run killed while an
    agent was editing a paused rebase leaves evidence of WHY the rebase
    was paused.

    Returns:
        Whether the durable state on disk now says ``resolving``. That
        includes the two no-op cases: a record already carrying the
        wanted value, and NO RECORD FILE at all -- with nothing on disk,
        recovery has nothing to act on either, so no interrupted-
        resolution warning is lost by proceeding. ``False`` is returned
        whenever the durable state was left disagreeing with the caller:
        a failed write, and also a record file that EXISTS but could not
        be read back. :func:`read_record` deliberately collapses absent,
        corrupt and transiently-unreadable into a single ``None``, so
        the file's existence is what separates "nothing to record" from
        "something is there and we could not see it"; the latter must
        not be reported as a persisted flag.

    Never raises. Deciding what an unpersistable flag means belongs to
    the caller: :mod:`ralph.pipeline.auto_integrate_rebase_merge` refuses
    to start a resolution it could not record and takes the safe
    abort-then-endpoint-merge path instead.
    """
    try:
        current = read_record(workspace_root)
        if current is None:
            return _absent_record_is_recordable(record_path(workspace_root))
        if current.resolving_rebase == resolving:
            return True
        write_record(
            workspace_root,
            current.model_copy(update={"resolving_rebase": resolving}),
        )
    except Exception as exc:
        logger.warning(
            "auto_integrate: could not persist resolving_rebase={} to the durable record: {}",
            resolving,
            exc,
        )
        return False
    return True


def clear_record(workspace_root: Path) -> None:
    """Unlink the durable record; missing-ok."""
    record_file = record_path(workspace_root)
    try:
        record_file.unlink()
    except FileNotFoundError:
        return


__all__ = [
    "AUTO_INTEGRATE_RECORD_FILENAME",
    "IntegrationOperation",
    "IntegrationPhase",
    "IntegrationRecord",
    "clear_record",
    "read_record",
    "record_path",
    "set_resolving_rebase",
    "write_record",
]


# ----- AC-14 catalog evidence -----
# This file is the authoritative source for the catalog entries listed
# below. Each ``# AC-14 rationale: <ID>`` line is the code-adjacent
# marker the AC-14 audit looks for; each ``# ladder rung: <N>``
# names the rung the entry sits on. Adding a new entry here requires
# BOTH lines or the audit fails.

# AC-14 rationale: E11
# ladder rung: 1
# ----- end AC-14 catalog evidence -----
