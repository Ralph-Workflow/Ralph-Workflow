from __future__ import annotations

import errno
import json
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from loguru import logger
from pydantic import ConfigDict

from ralph.git.resolution_failure_cause import ResolutionFailureCause
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
from ralph.pipeline._integration_action import RebaseOutcomeKind
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

    from ralph.pipeline.auto_integrate_resolution_state import ContainmentVerdict
    from ralph.pipeline.rebase_state import RebaseState


AUTO_INTEGRATE_RECORD_FILENAME = "auto_integrate_in_progress.json"

IntegrationPhase = Literal["integrating", "integrated"]
IntegrationOperation = Literal["feature_integrate", "target_reconcile"]

_VALID_PHASES: frozenset[str] = frozenset({"integrating", "integrated"})


class IntegrationRecord(RalphBaseModel):
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
    diagnostic_intervention_required: bool = False
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
    resolution_gate_key: tuple[str | None, tuple[str, ...], str | None] | None = None
    waiting_notification_key: tuple[str | None, tuple[str, ...], str | None] | None = None

    @property
    def is_integrated(self) -> bool:
        return self.phase in ("integrated",)

    @property
    def is_integrating(self) -> bool:
        return self.phase in ("integrating",)

    def with_transition(
        self,
        transition: Transition,
        **fields: object,
    ) -> IntegrationRecord:
        if not isinstance(transition, Transition) or transition not in Transition.ALL:
            raise ValueError(f"Invalid transition: {transition!r}")
        return transition.apply(self, **fields)

    def with_resolution_gate_key(
        self,
        gate_key: tuple[str | None, tuple[str, ...], str | None] | None,
    ) -> IntegrationRecord:
        return self.model_copy(update={"resolution_gate_key": gate_key})

    def with_waiting_notification_key(
        self,
        notification_key: tuple[str | None, tuple[str, ...], str | None] | None,
    ) -> IntegrationRecord:
        return self.model_copy(update={"waiting_notification_key": notification_key})


_LEGACY_GLOBAL_EVIDENCE_KEYS: frozenset[str] = frozenset({"for-each-ref", "ls-files"})


def _is_legacy_global_evidence_key(key: str) -> bool:
    if key in _LEGACY_GLOBAL_EVIDENCE_KEYS:
        return True
    return any(
        key.startswith(f"{prefix}:") or key.startswith(f"{prefix} ")
        for prefix in _LEGACY_GLOBAL_EVIDENCE_KEYS
    )


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
    migrated: list[tuple[str, str]] = []
    drop_marker: str | None = None
    for key, value in raw:
        if _is_legacy_global_evidence_key(key):
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
                migrated.append((key, value))
    return tuple(migrated), drop_marker


_RECORD_ROOT_BINDING: ContextVar[tuple[Path, Path] | None] = ContextVar(
    "integration_record_root_binding",
    default=None,
)


@contextmanager
def bind_integration_record_root(operation_root: Path, record_root: Path) -> Iterator[None]:
    token = _RECORD_ROOT_BINDING.set((operation_root.resolve(), record_root))
    try:
        yield
    finally:
        _RECORD_ROOT_BINDING.reset(token)


def record_path(workspace_root: Path) -> Path:
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
    record_file = record_path(workspace_root)
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
    pass


class RecordUnreadableError(RuntimeError):
    pass


class RecordReadOutcomeDisposition(StrEnum):
    ABSENT = "absent"
    PRESENT = "present"
    BUSY = "busy"
    UNREADABLE = "unreadable"


RecordUnreadableReason = Literal["corrupt_json", "unsupported_schema", "backend_read_failure"]


class RecordReadOutcome(RalphBaseModel):
    model_config = ConfigDict(frozen=True)

    disposition: RecordReadOutcomeDisposition
    record: IntegrationRecord | None = None
    unreadable_reason: RecordUnreadableReason | None = None
    busy_attempt: int | None = None
    busy_deadline: float | None = None


def _outcome_from_read_error(exc: OSError | UnicodeError | KeyError) -> RecordReadOutcome:
    if isinstance(exc, OSError) and exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
        return RecordReadOutcome(disposition=RecordReadOutcomeDisposition.BUSY)
    return RecordReadOutcome(
        disposition=RecordReadOutcomeDisposition.UNREADABLE,
        unreadable_reason="backend_read_failure",
    )


def _parse_outcome_payload(raw_text: str) -> dict[str, object] | None:
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
    del busy_attempt, busy_clock
    record_file = record_path(workspace_root)
    outcome: RecordReadOutcome | None = None
    record_exists = False
    try:
        record_exists = backend.exists(record_file)
    except FileNotFoundError:
        pass
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
    if any(not _is_valid_collector_evidence_key(key) for key, _ in migrated_evidence):
        return _clear_legacy_ownership_and_intervention(data_raw)

    return _apply_evidence_migration(data_raw, migrated_evidence, drop_marker, sequence)


def _apply_evidence_migration(
    data_raw: dict[str, object],
    migrated_evidence: tuple[tuple[str, str], ...],
    drop_marker: str | None,
    original_sequence: tuple[tuple[str, str], ...],
) -> dict[str, object]:
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
    phase_value = data_raw.get("phase")
    if not isinstance(phase_value, str) or phase_value not in _VALID_PHASES:
        return None
    if data_raw.get("diagnostic_intervention_required") is True:
        data_raw = _clear_legacy_ownership_and_intervention(data_raw)
    migrated = _migrate_payload_evidence(data_raw)
    if migrated is None:
        return None
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
    if record is not None and record.reintegrate_pending:
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
            raise RuntimeError("owned reintegration cannot release its unfinished target landing")
    record_file = record_path(workspace_root)
    try:
        backend.unlink(record_file, missing_ok=True)
    except FileNotFoundError:
        return


def clear_record_on_containment(
    workspace_root: Path,
    verdict: ContainmentVerdict,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> bool:
    if not verdict.contained:
        return False
    clear_record(workspace_root, backend=backend)
    return True


def apply_containment_release(
    state: RebaseState,
    root: Path | None,
    verdict: ContainmentVerdict,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> RebaseState:
    if not verdict.contained:
        return state
    if root is not None:
        try:
            clear_record_on_containment(root, verdict, backend=backend)
        except RuntimeError as exc:
            logger.warning("auto_integrate: containment release withheld: {}", exc)
            return state
    strategy = (
        state.clear_conflict_strategy()
        if state.resolution_exhausted or state.conflict_strategy_index == 0
        else state.clear_resolution_exhausted()
    )
    cleared = strategy.model_copy(
        update={
            "last_outcome": None
            if state.last_outcome is RebaseOutcomeKind.CONFLICT
            else state.last_outcome,
            "unresolved_integration_carried": False,
            "integration_retry_attempt": 0,
            "legacy_checkpoint_blocked": False,
        }
    )
    if cleared.recovery_record_retained:
        return cleared.clear_recovery_record_retained()
    return cleared


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
    "apply_containment_release",
    "clear_record",
    "clear_record_on_containment",
    "parse_lease",
    "parse_record_payload",
    "read_record",
    "read_record_outcome",
    "record_path",
    "set_resolving_rebase",
    "transition_by_name",
    "write_record",
]
