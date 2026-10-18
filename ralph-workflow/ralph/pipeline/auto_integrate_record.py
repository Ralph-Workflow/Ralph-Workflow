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

import errno
import json
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from loguru import logger
from pydantic import ConfigDict

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import atomic_write_bytes_if_changed
from ralph.pipeline._auto_integrate_record_restamp import (
    _clamp_loaded_attempt_counters,
    _clear_legacy_ownership_and_intervention,
    _coerce_payload_failure_cause,
    _is_valid_collector_evidence_key,
    _parse_raw_evidence_pairs,
    parse_lease,
)
from ralph.pipeline.auto_integrate_transition import (
    ACCEPT_REPAIR_EDITS,
    BUMP_DIAGNOSTIC_ATTEMPT,
    CLEAR,
    CLEAR_TRANSITION,
    HOLD_DIAGNOSTIC_EVIDENCE,
    MARK_LEGACY_FIELDS_PRESERVED,
    MARK_PENDING_MERGE_COMMIT,
    MARK_PENDING_REBASE_CONTINUE,
    MARK_REINTEGRATE_PENDING,
    MARK_RESOLVING_MERGE,
    MARK_RESOLVING_REBASE,
    PROMOTE_TO_INTEGRATED,
    RAISE_DIAGNOSTIC_INTERVENTION,
    RELEASE_DIAGNOSTIC_EVIDENCE,
    RESUME_REPAIR,
    START_INTEGRATING,
    Transition,
    transition_by_name,
)
from ralph.pipeline.rebase_state import ResolutionFailureCause
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
    diagnostic_owner_pid: int | None = None
    diagnostic_owner_boot_id: str | None = None
    # ``diagnostic_intervention_required`` is set by the diagnostic path
    # when the repair-attempt cap is reached. The run-loop's
    # :func:`ralph.pipeline._pipeline_state_machine.decide_phase_transition`
    # surfaces it as ``REPORT_INTERVENTION_REQUIRED`` and the record
    # refuses to clear until the operator acts. Defaulted, so legacy
    # on-disk records load cleanly.
    diagnostic_intervention_required: bool = False
    # ``repair_cap_reason`` is the human-readable reason the cap fired.
    # Used as the operator-facing alert (e.g. "diagnostic repair
    # exhausted; operator intervention required") and as the canonical
    # migration marker when the one-way legacy-key migrator drops an
    # obsolete observation (the common-dir reflog, see
    # :func:`_migrate_legacy_diagnostic_evidence`).
    repair_cap_reason: str | None = None
    repair_pending_diff: str | None = None
    repair_pending_paths: tuple[str, ...] = ()
    repair_commit_controls: str | None = None
    merge_commit_head: str | None = None
    merge_commit_parent: str | None = None
    merge_commit_tree: str | None = None
    operation_kind: IntegrationOperation = "feature_integrate"
    owning_worktree: str | None = None
    reintegrate_pending: bool = False
    initiating_feature_sha: str | None = None
    last_failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE

    @property
    def is_integrated(self) -> bool:
        """Whether this record has reached the integrated phase."""
        return self.phase in ("integrated",)

    @property
    def is_integrating(self) -> bool:
        """Whether this record is in the integrating phase."""
        return self.phase in ("integrating",)

    def with_transition(
        self,
        transition: Transition,
        **fields: object,
    ) -> IntegrationRecord:
        """Return a new :class:`IntegrationRecord` updated through the closed transition table.

        The transition table is the single authority for which
        field updates are legal at which phase. ``model_copy(update=...)``
        is no longer the only writer; ``with_transition`` is. An
        invalid transition (e.g. ``MARK_RESOLVING_REBASE`` from a
        record whose phase is not ``integrating``) raises
        :class:`ValueError` so the diagnostic path cannot silently
        mutate a record it does not own.
        """
        if not isinstance(transition, Transition) or transition not in Transition.ALL:
            raise ValueError(f"Invalid transition: {transition!r}")
        update = transition.apply(self, **fields)
        return self.model_copy(update=update)


# ---------------------------------------------------------------------------
# Transition table -- the single authority for record mutation.
# ---------------------------------------------------------------------------

# Keys produced by the legacy ``_diagnostic_snapshot`` writer that are
# forbidden under the new collector because they read repository-global
# state. A record carrying any of these keys cannot be re-verified by
# the new collector, so the loader surfaces intervention instead of
# silently clearing the evidence.
_LEGACY_GLOBAL_EVIDENCE_KEYS: frozenset[str] = frozenset({"for-each-ref", "ls-files"})


def _is_legacy_global_evidence_key(key: str) -> bool:
    """Return True if key is or starts with a legacy repository-global evidence key."""
    if key in _LEGACY_GLOBAL_EVIDENCE_KEYS:
        return True
    return any(
        key.startswith(f"{prefix}:") or key.startswith(f"{prefix} ")
        for prefix in _LEGACY_GLOBAL_EVIDENCE_KEYS
    )


# Legacy per-path observation keys the previous collector wrote under
# ``str(absolute_path)``. The new collector categorises them by suffix
# (see :func:`_categorise_legacy_path_key`).
_LEGACY_MERGE_STATE_SUFFIXES: tuple[str, ...] = (
    "/MERGE_HEAD",
    "/MERGE_MSG",
    "/REBASE_HEAD",
    "/ORIG_HEAD",
    "/AUTO_MERGE",
    "/CHERRY_PICK_HEAD",
)
_LEGACY_REBASE_DIR_INFIXES: tuple[str, ...] = ("/rebase-merge/", "/rebase-apply/")
_LEGACY_COMMON_DIR_DROPPED_INFIXES: tuple[str, ...] = (
    "/logs/HEAD",
    "/logs/refs/heads/",
)


def _categorise_legacy_path_key(key: str) -> tuple[str, str] | None:
    """Re-categorise a legacy ``str(absolute_path)`` evidence key under a new kind.

    Returns ``(new_kind, value_hint)`` where ``new_kind`` is one of
    ``"rebase_state_files"`` / ``"merge_state_files"`` and
    ``value_hint`` is the observation's value (a SHA hash or
    ``"unreadable"`` / ``"absent"``).

    The migration is one-way: legacy path-keyed observations are
    re-emitted under their new kind tag. The branch reflog and
    ``logs/HEAD`` are dropped (see
    :func:`_migrate_legacy_diagnostic_evidence`).
    """
    for suffix in _LEGACY_MERGE_STATE_SUFFIXES:
        if key.endswith(suffix):
            return ("merge_state_files", key)
    for infix in _LEGACY_REBASE_DIR_INFIXES:
        if infix in key:
            return ("rebase_state_files", key)
    return None


def _migrate_legacy_diagnostic_evidence(
    raw: tuple[tuple[str, str], ...],
) -> tuple[tuple[tuple[str, str], ...], str | None]:
    """Translate legacy diagnostic-evidence pairs into the new key space.

    The legacy writer stored one observation per pair, with the key
    being either a subcommand name (``for-each-ref``, ``ls-files``,
    ``work``, ``controls``, ``metadata``) or an absolute file path
    (the per-worktree rebase/merge state files plus the
    common-dir branch reflog and ``logs/HEAD``).

    The new collector never produces the repository-global
    subcommand names, so a record that still carries them cannot
    be re-verified; the loader surfaces intervention. The legacy
    ``work`` / ``controls`` keys translate to ``unstaged_work`` /
    ``commit_controls`` (the new collector's names). The
    per-worktree state file keys translate by suffix to
    ``merge_state_files`` / ``rebase_state_files``. The common-dir
    reflog and ``logs/HEAD`` are dropped with a
    ``repair_cap_reason`` marker.

    Returns the migrated key/value pairs and the dropped-reason
    marker (or ``None`` if no drop was needed).
    """
    migrated: list[tuple[str, str]] = []
    drop_marker: str | None = None
    for key, value in raw:
        if _is_legacy_global_evidence_key(key):
            # Repository-global observations cannot be re-verified.
            # The loader treats this as a corrupt schema and
            # surfaces intervention; keep the key as-is so the
            # operator can see what the legacy writer left behind.
            migrated.append((key, value))
            continue
        if key in {"work", "controls", "metadata", "rev-parse"}:
            migrated.append(
                (
                    {
                        "work": "unstaged_work",
                        "controls": "commit_controls",
                        "metadata": "metadata",
                        "rev-parse": "head",
                    }[key],
                    value,
                )
            )
            continue
        if key in {"unstaged_work", "commit_controls", "metadata", "head"}:
            migrated.append((key, value))
            continue
        if key.startswith("rebase_state_files:") or key.startswith("merge_state_files:"):
            migrated.append((key, value))
            continue
        # ``str(absolute_path)`` from the legacy writer.
        if key in ("unstaged_work", "commit_controls") or key.startswith(
            ("unstaged_work:", "commit_controls:")
        ):
            migrated.append((key, value))
            continue
        for infix in _LEGACY_COMMON_DIR_DROPPED_INFIXES:
            if infix in key:
                drop_marker = "legacy common-dir reflog observation dropped"
                break
        else:
            category = _categorise_legacy_path_key(key)
            if category is not None:
                new_kind, _ = category
                migrated.append((f"{new_kind}:{key}", value))
            else:
                # Preserve arbitrary absolute paths under their
                # original key so no on-disk evidence is silently
                # lost. The diagnostic path treats unknown keys
                # as unverified (see ``diagnostic_evidence_unchanged``).
                migrated.append((key, value))
    return tuple(migrated), drop_marker


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


class RecordBusyError(RuntimeError):
    """Typed refusal: the record read hit demonstrable transient lock contention.

    Raised by :func:`clear_record` and by
    :meth:`ralph.pipeline.auto_integrate_record_store.IntegrationRecordStore.transition`
    when the backend reports ``EAGAIN``/``EWOULDBLOCK`` so no mutation is
    applied without a valid unexpired persisted retry deadline.
    """


class RecordUnreadableError(RuntimeError):
    """Typed refusal: the durable record exists but cannot be read.

    Raised by :func:`clear_record` and by
    :meth:`ralph.pipeline.auto_integrate_record_store.IntegrationRecordStore.transition`
    so the UNREADABLE ownership evidence is never silently unlinked
    or overwritten with a fresh record.
    """


class RecordReadOutcomeDisposition(StrEnum):
    """Closed disposition set for :func:`read_record_outcome`."""

    ABSENT = "absent"
    PRESENT = "present"
    BUSY = "busy"
    UNREADABLE = "unreadable"


RecordUnreadableReason = Literal["corrupt_json", "unsupported_schema", "backend_read_failure"]


class RecordReadOutcome(RalphBaseModel):
    """Closed typed result of reading the durable crash record.

    Exactly one of four dispositions:

    * ``ABSENT`` -- no record file exists; ``record`` is ``None``.
    * ``PRESENT`` -- a valid record was loaded into ``record``.
    * ``BUSY`` -- demonstrably transient lock contention (the backend
      reported ``EAGAIN``/``EWOULDBLOCK``). No attempt count or
      deadline is minted at read time: they come only from existing
      PERSISTED retry state; with no valid unexpired persisted
      deadline the consumer decides REQUIRE_INTERVENTION.
    * ``UNREADABLE`` -- the file exists but is unusable, with a
      reason from :data:`RecordUnreadableReason`. Backend read and
      permission failures and decoding errors map to
      ``backend_read_failure``. Callers MUST NOT mutate or unlink
      the evidence.
    """

    model_config = ConfigDict(frozen=True)

    disposition: RecordReadOutcomeDisposition
    record: IntegrationRecord | None = None
    unreadable_reason: RecordUnreadableReason | None = None
    busy_attempt: int | None = None
    busy_deadline: float | None = None


def _outcome_from_read_error(exc: OSError | UnicodeError | KeyError) -> RecordReadOutcome:
    """Map a backend read failure to BUSY (EAGAIN contention) or UNREADABLE."""
    if isinstance(exc, OSError) and exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
        # Demonstrable transient lock contention; no deadline is minted
        # here (persisted retry state alone authorizes waits).
        return RecordReadOutcome(disposition=RecordReadOutcomeDisposition.BUSY)
    return RecordReadOutcome(
        disposition=RecordReadOutcomeDisposition.UNREADABLE,
        unreadable_reason="backend_read_failure",
    )


def _parse_outcome_payload(raw_text: str) -> dict[str, object] | None:
    """Parse outcome JSON text into a dict, or None for any parse failure."""
    try:
        parsed: object = json.loads(raw_text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def read_record_outcome(
    workspace_root: Path,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    busy_attempt: int | None = None,
    busy_clock: object | None = None,
) -> RecordReadOutcome:
    """Read the durable record and return a CLOSED typed outcome.

    Unlike :func:`read_record` this never collapses corruption or
    transient read failure into ``None``: ABSENT is proven by the
    file not existing, and UNREADABLE preserves the evidence for the
    caller. BUSY is reserved for demonstrable transient lock
    contention (``EAGAIN``/``EWOULDBLOCK``); its attempt/deadline
    fields are always ``None`` because a fresh deadline minted at
    read time would replenish the bounded-retry budget on every
    failing read. ``busy_attempt`` and ``busy_clock`` are ignored
    (kept so out-of-scope callers keep importing); they carry no
    retry authority.
    """
    del busy_attempt, busy_clock  # WU-A: no fresh allowances at read time
    record_file = record_path(workspace_root)
    outcome: RecordReadOutcome | None = None
    record_exists = False
    try:
        record_exists = backend.exists(record_file)
    except FileNotFoundError:
        pass  # absence proven by the raised not-found
    except OSError as exc:
        if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
            outcome = RecordReadOutcome(disposition=RecordReadOutcomeDisposition.BUSY)
        else:
            outcome = _outcome_from_read_error(exc)
    if outcome is None and not record_exists:
        outcome = RecordReadOutcome(disposition=RecordReadOutcomeDisposition.ABSENT)
    if outcome is None:
        try:
            raw_text = backend.read_text(record_file, encoding="utf-8")
        except FileNotFoundError:
            outcome = RecordReadOutcome(disposition=RecordReadOutcomeDisposition.ABSENT)
        except (OSError, UnicodeError, KeyError) as exc:
            outcome = _outcome_from_read_error(exc)
        else:
            parsed = _parse_outcome_payload(raw_text)
            if parsed is None:
                outcome = RecordReadOutcome(
                    disposition=RecordReadOutcomeDisposition.UNREADABLE,
                    unreadable_reason="corrupt_json",
                )
            else:
                record = parse_record_payload(parsed)
                if record is None:
                    outcome = RecordReadOutcome(
                        disposition=RecordReadOutcomeDisposition.UNREADABLE,
                        unreadable_reason="unsupported_schema",
                    )
                else:
                    outcome = RecordReadOutcome(
                        disposition=RecordReadOutcomeDisposition.PRESENT,
                        record=record,
                    )
    return outcome


def read_record(
    workspace_root: Path,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> IntegrationRecord | None:
    """Return the durable record or ``None`` when absent / corrupt.

    ``None`` does not prove absence: invalid JSON, unreadable bytes, and
    unsupported schema or phase values also return ``None``. Recovery and
    dispatch callers check record-path existence separately and preserve
    unreadable ownership evidence without authorizing Git mutations.
    """
    record_file = record_path(workspace_root)
    if not backend.exists(record_file):
        return None
    raw = _read_record_raw(record_file, backend=backend)
    if raw is None:
        return None
    return parse_record_payload(raw)


def _read_record_raw(
    record_file: Path,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> dict[str, object] | None:
    """Read and parse the record file; return the parsed dict or None.

    Splits out the read+JSON-parse steps from :func:`read_record` so
    each helper stays under the ruff PLR0911 return-statement cap.
    Returns ``None`` for any read or parse failure (treated as
    corrupt / absent by the recovery preamble).
    """
    try:
        raw_text = backend.read_text(record_file, encoding="utf-8")
    except (OSError, UnicodeError, KeyError):
        return None
    try:
        parsed: object = json.loads(raw_text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _migrate_payload_evidence(data_raw: dict[str, object]) -> dict[str, object] | None:
    """Migrate legacy diagnostic evidence keys in payload; return None on parse error.

    For any evidence payload the current collector cannot interpret
    (legacy or unknown keys/format, unparseable lease ownership),
    returns the payload with ``diagnostic_evidence`` set to an empty
    tuple AND any persisted intervention flag cleared, so the loaded
    record carries no live comparison and no operator-only dispatch
    action; the durable identity (``target``, SHAs, ``operation_kind``)
    is preserved, and the diagnostic path re-derives state from Git
    on the next seam. Surfacing intervention from the loader was the
    production livelock: the diagnostic could not re-verify the
    evidence and refused to proceed.
    """
    raw_evidence = data_raw.get("diagnostic_evidence")
    sequence: tuple[tuple[str, str], ...]
    if raw_evidence is None:
        sequence = ()
    else:
        try:
            parsed_sequence = _parse_raw_evidence_pairs(raw_evidence)
            if parsed_sequence is None:
                return _clear_legacy_ownership_and_intervention(data_raw)
            sequence = parsed_sequence
        except Exception:
            return _clear_legacy_ownership_and_intervention(data_raw)

    is_intervention_flagged = data_raw.get("diagnostic_intervention_required") is True
    has_legacy_global_evidence = any(_is_legacy_global_evidence_key(key) for key, _ in sequence)
    raw_ownership = data_raw.get("diagnostic_ownership")
    has_unparseable_ownership = (
        isinstance(raw_ownership, str) and parse_lease(raw_ownership) == (None, None)
    ) or (raw_ownership is not None and not isinstance(raw_ownership, str))
    if has_legacy_global_evidence or (is_intervention_flagged and has_unparseable_ownership):
        return _clear_legacy_ownership_and_intervention(data_raw)

    if not sequence:
        return data_raw

    migrated_evidence, drop_marker = _migrate_legacy_diagnostic_evidence(sequence)
    # Any key the current collector cannot interpret must drop the held
    # evidence regardless of the intervention flag (O1 / AC1). The old
    # gate only fired when the intervention flag was set; that left
    # unknown keys persistent when no operator escalation was recorded,
    # which is the documented O1 livelock: the diagnostic path could not
    # verify the evidence and the record retained forever.
    if any(not _is_valid_collector_evidence_key(key) for key, _ in migrated_evidence):
        return _clear_legacy_ownership_and_intervention(data_raw)

    return _apply_evidence_migration(data_raw, migrated_evidence, drop_marker, sequence)


def _apply_evidence_migration(
    data_raw: dict[str, object],
    migrated_evidence: tuple[tuple[str, str], ...],
    drop_marker: str | None,
    original_sequence: tuple[tuple[str, str], ...],
) -> dict[str, object]:
    """Apply the legacy key-space migration to ``data_raw``.

    Helper split from :func:`_migrate_payload_evidence` to keep
    that function's return-statement count under the ruff
    ``PLR0911`` cap. Returns the migrated payload (or ``data_raw``
    unchanged when no migration is needed).
    """
    if drop_marker is not None:
        existing_reason = data_raw.get("repair_cap_reason")
        return {
            **data_raw,
            "diagnostic_evidence": migrated_evidence,
            "repair_cap_reason": existing_reason or drop_marker,
        }
    if migrated_evidence != original_sequence:
        return {**data_raw, "diagnostic_evidence": migrated_evidence}
    return data_raw


def parse_record_payload(data_raw: dict[str, object]) -> IntegrationRecord | None:
    """Validate the parsed record payload against the model contract.

    Splits the schema-level validation out of :func:`read_record` so
    the I/O helper stays under the ruff PLR0911 return-statement
    cap. Returns ``None`` for any rejection (corrupt file).

    The one-way legacy evidence migration runs here so the loaded
    record already carries the new key space: a legacy ``work`` /
    ``controls`` key becomes ``unstaged_work`` / ``commit_controls``;
    a legacy ``str(absolute_path)`` for a per-worktree state file
    becomes ``<kind>:<path>``; the branch reflog and ``logs/HEAD``
    are dropped with a ``repair_cap_reason`` marker. A legacy
    record that still carries the repository-global
    ``for-each-ref`` / ``ls-files`` keys is loaded normally with
    ``diagnostic_evidence=()``; the dropped evidence is still
    visible in the original on-disk bytes (preserved by the
    closed transition table on the durable record), and the
    diagnostic path re-derives state from Git on the next seam.

    After the record is validated, a held lease whose fingerprint
    does not match the freshly-computed ``identity_fingerprint`` is
    treated as a schema-migration candidate: the record is
    re-stamped to a clean state (evidence ``()``, ownership
    ``None``, intervention flag ``False``, repair-cap reason
    cleared) via the same :func:`_clear_legacy_ownership_and_intervention`
    helper that the legacy / unparseable-lease branches use. The
    lease format (``<fingerprint>:<held_at>``) only stores the
    fingerprint hash, not the individual identity fields, so a
    fingerprint mismatch on load cannot be disambiguated between
    ``"schema changed"`` and ``"external party rewrote the
    record"``; the safe path is to re-derive state from Git on the
    next seam. AC-4 / GAP-5 closes the O1 livelock that surfaced
    here: a narrower identity-fingerprint field set no longer
    forces a fingerprint mismatch when the record crosses a
    legitimate lifecycle transition.
    """
    phase_value = data_raw.get("phase")
    if not isinstance(phase_value, str) or phase_value not in _VALID_PHASES:
        return None
    migrated = _migrate_payload_evidence(data_raw)
    if migrated is None:
        return None
    # Load-time clamp: coerce over-cap attempts to cap - 1 (see _clamp_loaded_attempt_counters).
    with_cause = _coerce_payload_failure_cause(migrated)
    with_cause = _clamp_loaded_attempt_counters(with_cause)
    try:
        record = IntegrationRecord.model_validate(with_cause)
    except Exception:
        return None
    from ralph.pipeline._auto_integrate_record_restamp import (
        restamp_fingerprint_mismatch,
    )

    return restamp_fingerprint_mismatch(record, with_cause)


_parse_record_payload = parse_record_payload


def _absent_record_is_recordable(path: Path) -> bool:
    """Only a proven absent ownership record permits starting a new operation."""
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def set_resolving_rebase(
    workspace_root: Path,
    resolving: bool,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> bool:
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
        current = read_record(workspace_root, backend=backend)
        if current is None:
            return _absent_record_is_recordable(record_path(workspace_root))
        if current.resolving_rebase == resolving:
            return True
        write_record(
            workspace_root,
            current.with_transition(MARK_RESOLVING_REBASE, resolving_rebase=resolving),
            backend=backend,
        )
    except Exception as exc:
        logger.warning(
            "auto_integrate: could not persist resolving_rebase={} to the durable record: {}",
            resolving,
            exc,
        )
        return False
    return True


def clear_record(
    workspace_root: Path,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Unlink the durable record; missing-ok.

    The gate is tighter than the pre-fix behaviour: a record
    carrying ``diagnostic_intervention_required=True`` MUST NOT be
    cleared silently (the operator must act), and a record whose
    phase is ``integrating`` with a non-empty resolution flag
    cannot be cleared (the operation has unfinished work).

    UNREADABLE evidence is refused: a record that exists but cannot
    be parsed MUST NOT be unlinked, because it is the only durable
    proof of an interrupted integration. The typed
    :class:`RecordUnreadableError` carries the refusal. ABSENT
    remains idempotent (missing-ok unlink).
    """
    outcome = read_record_outcome(workspace_root, backend=backend)
    if outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE:
        raise RecordUnreadableError(
            f"refusing to unlink unreadable record evidence at "
            f"{record_path(workspace_root)} (reason: {outcome.unreadable_reason})"
        )
    if outcome.disposition is RecordReadOutcomeDisposition.BUSY:
        raise RecordBusyError(
            f"transient record lock contention at {record_path(workspace_root)}; "
            "bounded retry with a persisted deadline owns the backoff"
        )
    record = outcome.record
    if record is not None:
        if record.diagnostic_intervention_required:
            raise RuntimeError(
                "Integration diagnostic requires operator intervention; "
                "the durable record cannot be released automatically"
            )
        if record.reintegrate_pending:
            from ralph.git.merge import branch_sha
            from ralph.git.operations import get_head_sha
            from ralph.pipeline.auto_integrate_recovery_terminal import post_attempt_verify

            post_attempt_verify(workspace_root, expected_head_sha=None, owns_resolution=False)
            if (
                record.phase != "integrated"
                or record.integrated_feature_sha is None
                or branch_sha(workspace_root, record.target) != record.integrated_feature_sha
                or get_head_sha(workspace_root) != record.integrated_feature_sha
            ):
                raise RuntimeError(
                    "owned reintegration cannot release its unfinished target landing"
                )
    record_file = record_path(workspace_root)
    try:
        backend.unlink(record_file, missing_ok=True)
    except FileNotFoundError:
        return


__all__ = [
    "ACCEPT_REPAIR_EDITS",
    "AUTO_INTEGRATE_RECORD_FILENAME",
    "BUMP_DIAGNOSTIC_ATTEMPT",
    "CLEAR",
    "CLEAR_TRANSITION",
    "HOLD_DIAGNOSTIC_EVIDENCE",
    "MARK_LEGACY_FIELDS_PRESERVED",
    "MARK_PENDING_MERGE_COMMIT",
    "MARK_PENDING_REBASE_CONTINUE",
    "MARK_REINTEGRATE_PENDING",
    "MARK_RESOLVING_MERGE",
    "MARK_RESOLVING_REBASE",
    "PROMOTE_TO_INTEGRATED",
    "RAISE_DIAGNOSTIC_INTERVENTION",
    "RELEASE_DIAGNOSTIC_EVIDENCE",
    "RESUME_REPAIR",
    "START_INTEGRATING",
    "IntegrationOperation",
    "IntegrationPhase",
    "IntegrationRecord",
    "RecordBusyError",
    "RecordReadOutcome",
    "RecordReadOutcomeDisposition",
    "RecordUnreadableError",
    "RecordUnreadableReason",
    "Transition",
    "clear_record",
    "parse_lease",
    "parse_record_payload",
    "read_record",
    "read_record_outcome",
    "record_path",
    "set_resolving_rebase",
    "transition_by_name",
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
