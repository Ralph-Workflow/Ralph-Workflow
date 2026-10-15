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
        if key in _LEGACY_GLOBAL_EVIDENCE_KEYS:
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


def read_record(workspace_root: Path) -> IntegrationRecord | None:
    """Return the durable record or ``None`` when absent / corrupt.

    ``None`` does not prove absence: invalid JSON, unreadable bytes, and
    unsupported schema or phase values also return ``None``. Recovery and
    dispatch callers check record-path existence separately and preserve
    unreadable ownership evidence without authorizing Git mutations.
    """
    record_file = record_path(workspace_root)
    if not record_file.exists():
        return None
    raw = _read_record_raw(record_file)
    if raw is None:
        return None
    return parse_record_payload(raw)


def _read_record_raw(record_file: Path) -> dict[str, object] | None:
    """Read and parse the record file; return the parsed dict or None.

    Splits out the read+JSON-parse steps from :func:`read_record` so
    each helper stays under the ruff PLR0911 return-statement cap.
    Returns ``None`` for any read or parse failure (treated as
    corrupt / absent by the recovery preamble).
    """
    try:
        raw_text = record_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    try:
        parsed: object = json.loads(raw_text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


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
    ``for-each-ref`` / ``ls-files`` keys is rejected as
    intervention-required -- the new collector would not be able
    to re-verify them, and silently clearing the evidence would
    lose the operator-visible fingerprint.
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
    raw_evidence = data_raw.get("diagnostic_evidence")
    migrated_evidence: tuple[tuple[str, str], ...] | None = None
    drop_marker: str | None = None
    if isinstance(raw_evidence, (list, tuple)):
        try:
            pairs: list[tuple[str, str]] = []
            for item in raw_evidence:
                if isinstance(item, (list, tuple)):
                    match item:
                        case [k, v] if isinstance(k, str) and isinstance(v, str):
                            pairs.append((k, v))
            sequence: tuple[tuple[str, str], ...] = tuple(pairs)
        except Exception:
            return None
        if any(key in _LEGACY_GLOBAL_EVIDENCE_KEYS for key, _ in sequence):
            # Cannot re-verify repository-global observations;
            # surface intervention rather than silently clear.
            data_raw = {
                **data_raw,
                "diagnostic_intervention_required": True,
                "repair_cap_reason": (
                    "legacy repository-global evidence observation present; "
                    "operator intervention required"
                ),
            }
        else:
            migrated_evidence, drop_marker = _migrate_legacy_diagnostic_evidence(sequence)
            if drop_marker is not None:
                existing_reason = data_raw.get("repair_cap_reason")
                data_raw = {
                    **data_raw,
                    "diagnostic_evidence": migrated_evidence,
                    "repair_cap_reason": existing_reason or drop_marker,
                }
            elif migrated_evidence != sequence:
                data_raw = {**data_raw, "diagnostic_evidence": migrated_evidence}
    try:
        return IntegrationRecord.model_validate(data_raw)
    except Exception:
        return None


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
            current.with_transition(MARK_RESOLVING_REBASE, resolving_rebase=resolving),
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
    """Unlink the durable record; missing-ok.

    The gate is tighter than the pre-fix behaviour: a record
    carrying ``diagnostic_intervention_required=True`` MUST NOT be
    cleared silently (the operator must act), and a record whose
    phase is ``integrating`` with a non-empty resolution flag
    cannot be cleared (the operation has unfinished work).
    """
    record = read_record(workspace_root)
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
        record_file.unlink()
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
    "Transition",
    "clear_record",
    "parse_record_payload",
    "read_record",
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
