from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.git.merge import (
    MERGE_STATE_NONE,
    MERGE_STATE_UNKNOWN,
    merge_state,
    observe_branch_sha,
)
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.git.subprocess_runner import run_git
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.pipeline._rebase_outcome_kind import RebaseOutcomeKind
from ralph.pipeline.integration_dispatch_blocked_error import IntegrationDispatchBlockedError
from ralph.pipeline.integration_observation import _decode_quoted_porcelain_path
from ralph.pipeline.integration_resolution_status import IntegrationResolutionStatus
from ralph.pipeline.integration_resolution_types import IntegrationResolutionVerdict
from ralph.pipeline.rebase_state import RebaseState
from ralph.policy.resolution_names import RESOLUTION_DRAIN_NAME

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.rebase_state import RebaseState

RESOLUTION_DRAIN = RESOLUTION_DRAIN_NAME

_PORCELAIN_MIN_ENTRY_LEN = 4

_BOTH_SIDES_UNMERGED_CODES = frozenset({"AA", "DD"})


RESOLVED = IntegrationResolutionStatus.RESOLVED
RECOVERABLE = IntegrationResolutionStatus.RECOVERABLE
EXHAUSTED = IntegrationResolutionStatus.EXHAUSTED


class IntegrationBlockKind(StrEnum):
    UNMERGED_PATHS = "unmerged_paths"
    REBASE_IN_PROGRESS = "rebase_in_progress"
    MERGE_IN_PROGRESS = "merge_in_progress"


class UnknownKind(StrEnum):
    PORCELAIN_UNREADABLE = "porcelain_unreadable"
    REBASE_STATE_UNREADABLE = "rebase_state_unreadable"
    MERGE_STATE_UNREADABLE = "merge_state_unreadable"


class IntegrationState(StrEnum):
    KNOWN_GOOD = "known_good"
    KNOWN_BLOCKED = "known_blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class KnownGood:
    pass


@dataclass(frozen=True)
class KnownBlocked:
    kind: IntegrationBlockKind
    detail: str


@dataclass(frozen=True)
class Unknown:
    kind: UnknownKind


ProbeOutcome = KnownGood | KnownBlocked | Unknown


@dataclass(frozen=True)
class TreeReadinessAssessment:
    development_dispatch_allowed: bool
    integration_ready: bool
    blocking_reasons: tuple[str, ...] = ()
    block_kinds: tuple[IntegrationBlockKind, ...] = ()
    unknown_kinds: tuple[UnknownKind, ...] = ()


@dataclass(frozen=True)
class _AssessedIntegrationResolutionVerdict(IntegrationResolutionVerdict):
    development_dispatch_allowed: bool = True
    integration_ready: bool = True
    blocking_reasons: tuple[str, ...] = ()
    block_kinds: tuple[IntegrationBlockKind, ...] = ()
    unknown_kinds: tuple[UnknownKind, ...] = ()

    @property
    def integration_state(self) -> IntegrationState:
        if self.block_kinds or not self.dispatch_allowed:
            return IntegrationState.KNOWN_BLOCKED
        if self.unknown_kinds:
            return IntegrationState.UNKNOWN
        return IntegrationState.KNOWN_GOOD


def _inspect_porcelain_uncommitted_state(porcelain_output: str) -> tuple[bool, bool]:
    has_staged = False
    has_modified_tracked = False
    for raw_line in porcelain_output.splitlines():
        if len(raw_line) < _PORCELAIN_MIN_ENTRY_LEN:
            continue
        code = raw_line[:2]
        x, y = code[0], code[1]
        if (x, y) == ("?", "?") or (x, y) == ("!", "!"):
            continue
        if x in ("M", "A", "D", "R", "C", "T"):
            has_staged = True
        if y in ("M", "D", "T"):
            has_modified_tracked = True
    return has_staged, has_modified_tracked


def _uncommitted_integration_reasons(porcelain_output: str) -> tuple[str, ...]:
    has_staged, has_modified_tracked = _inspect_porcelain_uncommitted_state(porcelain_output)
    reasons: list[str] = []
    if has_staged:
        reasons.append("staged uncommitted work blocks integration")
    if has_modified_tracked:
        reasons.append(
            "worktree not clean; uncommitted tracked changes deferred catch-up integration"
        )
    if reasons or not porcelain_output.strip():
        return tuple(reasons)
    for raw_line in porcelain_output.splitlines():
        if len(raw_line) >= _PORCELAIN_MIN_ENTRY_LEN and raw_line[:2] not in ("??", "!!"):
            return (
                "worktree not clean; uncommitted tracked changes deferred catch-up integration",
            )
    return ()


def _probe_porcelain(
    root: Path,
    probe: Callable[[Path], tuple[bool, str]],
) -> tuple[ProbeOutcome, str]:
    try:
        readable, porcelain_output = probe(root)
    except Exception as exc:
        logger.debug("integration resolution: porcelain probe failed: {}", exc)
        return Unknown(UnknownKind.PORCELAIN_UNREADABLE), ""
    if not readable:
        return Unknown(UnknownKind.PORCELAIN_UNREADABLE), ""
    unmerged = unmerged_porcelain_paths(porcelain_output)
    if unmerged:
        return (
            KnownBlocked(
                IntegrationBlockKind.UNMERGED_PATHS,
                "unmerged paths remain from an unfinished rebase or merge: " + ", ".join(unmerged),
            ),
            porcelain_output,
        )
    return KnownGood(), porcelain_output


def _probe_rebase(root: Path, rebase_active: Callable[[Path], bool]) -> ProbeOutcome:
    try:
        active = rebase_active(root)
    except Exception as exc:
        logger.debug("integration resolution: rebase-state probe failed: {}", exc)
        return Unknown(UnknownKind.REBASE_STATE_UNREADABLE)
    if active:
        return KnownBlocked(IntegrationBlockKind.REBASE_IN_PROGRESS, "rebase is in progress")
    return KnownGood()


def _probe_merge(root: Path, merge_status: Callable[[Path], str]) -> ProbeOutcome:
    try:
        state = merge_status(root)
    except Exception as exc:
        logger.debug("integration resolution: merge-state probe failed: {}", exc)
        return Unknown(UnknownKind.MERGE_STATE_UNREADABLE)
    if state == MERGE_STATE_UNKNOWN:
        return Unknown(UnknownKind.MERGE_STATE_UNREADABLE)
    if state != MERGE_STATE_NONE:
        return KnownBlocked(
            IntegrationBlockKind.MERGE_IN_PROGRESS,
            "merge is in progress or merge state is unreadable",
        )
    return KnownGood()


def _live_probe_outcomes(
    root: Path,
    *,
    porcelain: Callable[[Path], tuple[bool, str]] | None,
    rebase_active: Callable[[Path], bool],
    merge_status: Callable[[Path], str],
) -> tuple[tuple[ProbeOutcome, ...], str]:
    porcelain_outcome, porcelain_output = _probe_porcelain(root, porcelain or _full_porcelain)
    return (
        porcelain_outcome,
        _probe_rebase(root, rebase_active),
        _probe_merge(root, merge_status),
    ), porcelain_output


def assess_tree_readiness(
    root: Path,
    state: RebaseState | None = None,
    *,
    porcelain: Callable[[Path], tuple[bool, str]] | None = None,
    rebase_active: Callable[[Path], bool] = rebase_in_progress,
    merge_status: Callable[[Path], str] = merge_state,
) -> TreeReadinessAssessment:
    retained = _observe_retained_record(root)
    ownership = None if retained.already_landed else retained.reason
    if ownership is not None:
        return TreeReadinessAssessment(
            development_dispatch_allowed=False,
            integration_ready=False,
            blocking_reasons=(ownership,),
        )
    inspectable = not (
        porcelain is None and (not isinstance(root, Path) or not (root / ".git").exists())
    )
    if not inspectable:
        if state is not None:
            persisted = persisted_integration_resolution_verdict(state)
            if persisted is not None:
                return TreeReadinessAssessment(
                    development_dispatch_allowed=persisted.dispatch_allowed,
                    integration_ready=False,
                    blocking_reasons=persisted.reasons,
                    unknown_kinds=(UnknownKind.PORCELAIN_UNREADABLE,),
                )
        return TreeReadinessAssessment(
            development_dispatch_allowed=True,
            integration_ready=True,
        )

    outcomes, porcelain_output = _live_probe_outcomes(
        root,
        porcelain=porcelain,
        rebase_active=rebase_active,
        merge_status=merge_status,
    )
    blocked = tuple(outcome for outcome in outcomes if isinstance(outcome, KnownBlocked))
    unknown = tuple(outcome for outcome in outcomes if isinstance(outcome, Unknown))
    if blocked:
        return TreeReadinessAssessment(
            development_dispatch_allowed=False,
            integration_ready=False,
            blocking_reasons=tuple(outcome.detail for outcome in blocked),
            block_kinds=tuple(outcome.kind for outcome in blocked),
            unknown_kinds=tuple(outcome.kind for outcome in unknown),
        )
    if unknown:
        return TreeReadinessAssessment(
            development_dispatch_allowed=True,
            integration_ready=False,
            unknown_kinds=tuple(outcome.kind for outcome in unknown),
        )

    integration_blocking: tuple[str, ...] = _uncommitted_integration_reasons(porcelain_output)
    return TreeReadinessAssessment(
        development_dispatch_allowed=True,
        integration_ready=not bool(integration_blocking),
        blocking_reasons=integration_blocking,
    )


def _clean_assessed_verdict() -> _AssessedIntegrationResolutionVerdict:
    return _AssessedIntegrationResolutionVerdict(
        RESOLVED,
        (),
        None,
        development_dispatch_allowed=True,
        integration_ready=True,
        blocking_reasons=(),
    )


def _unknown_assessed_verdict(
    unknown_kinds: tuple[UnknownKind, ...],
) -> _AssessedIntegrationResolutionVerdict:
    critical_unknown = frozenset(unknown_kinds) == frozenset(UnknownKind)
    logger.info(
        "integration resolution: {} unreadable; integration state unknown, dispatch {}",
        ", ".join(kind.value for kind in unknown_kinds),
        "blocked until recovery can inspect it" if critical_unknown else "allowed",
    )
    if not critical_unknown:
        return _AssessedIntegrationResolutionVerdict(
            RESOLVED,
            (),
            None,
            development_dispatch_allowed=True,
            integration_ready=False,
            blocking_reasons=(),
            unknown_kinds=unknown_kinds,
        )
    return _AssessedIntegrationResolutionVerdict(
        RECOVERABLE,
        ("integration evidence is unreadable",),
        RESOLUTION_DRAIN,
        development_dispatch_allowed=False,
        integration_ready=False,
        blocking_reasons=("integration evidence is unreadable",),
        unknown_kinds=unknown_kinds,
    )


def _log_persisted_override(
    persisted: IntegrationResolutionVerdict, readiness: TreeReadinessAssessment
) -> None:
    if readiness.integration_ready:
        logger.info(
            "integration resolution: persisted {} evidence describes a repository that is "
            "now clean ({}); the live tree decides",
            persisted.status,
            "; ".join(persisted.reasons) or "no reason recorded",
        )
    else:
        logger.info(
            "integration resolution: persisted {} evidence describes a repository with "
            "uncommitted work ({}); development dispatch allowed, live tree decides",
            persisted.status,
            "; ".join(readiness.blocking_reasons),
        )


def correct_overtaken_persisted_record(state: RebaseState) -> RebaseState:
    corrected = state
    if corrected.last_outcome in (
        RebaseOutcomeKind.CONFLICT,
        RebaseOutcomeKind.UNKNOWN,
        RebaseOutcomeKind.LADDER_ESCALATION,
        RebaseOutcomeKind.INTERVENTION_REQUIRED,
        RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
    ):
        corrected = corrected.model_copy(
            update={
                "last_outcome": RebaseOutcomeKind.COMPLETED,
                "last_reason": (
                    "live clean tree overtook the persisted unresolved integration record; "
                    "correction written back"
                ),
            }
        )
    if corrected.integration_unresolved:
        corrected = corrected.model_copy(
            update={
                "unresolved_integration_carried": False,
                "legacy_checkpoint_blocked": False,
                "resolving_rebase": False,
                "resolving_merge": False,
                "diagnostic_ownership": None,
            }
        )
    if corrected.recovery_record_retained:
        corrected = corrected.clear_recovery_record_retained()
    if corrected.resolution_exhausted:
        corrected = corrected.clear_resolution_exhausted()
    return corrected


type WritePersistedCorrection = Callable[[RebaseState], None]


def _correct_persisted_record(
    state: RebaseState,
    readiness: TreeReadinessAssessment,
    write_back: WritePersistedCorrection | None,
) -> None:
    if not readiness.integration_ready or readiness.unknown_kinds:
        return
    corrected = correct_overtaken_persisted_record(state)
    if corrected is not state and write_back is not None:
        write_back(corrected)


def inspect_integration_resolution(
    root: Path,
    state: RebaseState,
    *,
    porcelain: Callable[[Path], tuple[bool, str]] | None = None,
    rebase_active: Callable[[Path], bool] = rebase_in_progress,
    merge_status: Callable[[Path], str] = merge_state,
    write_back: Callable[[RebaseState], None] | None = None,
) -> _AssessedIntegrationResolutionVerdict:
    persisted = persisted_integration_resolution_verdict(state)
    readiness = assess_tree_readiness(
        root,
        state=state,
        porcelain=porcelain,
        rebase_active=rebase_active,
        merge_status=merge_status,
    )

    if not readiness.development_dispatch_allowed:
        return _AssessedIntegrationResolutionVerdict(
            persisted.status if persisted is not None else RECOVERABLE,
            readiness.blocking_reasons,
            persisted.recovery_executor if persisted is not None else RESOLUTION_DRAIN,
            development_dispatch_allowed=False,
            integration_ready=False,
            blocking_reasons=readiness.blocking_reasons,
            block_kinds=readiness.block_kinds,
            unknown_kinds=readiness.unknown_kinds,
        )

    if readiness.unknown_kinds:
        if persisted is not None:
            return _AssessedIntegrationResolutionVerdict(
                persisted.status,
                persisted.reasons,
                persisted.recovery_executor,
                development_dispatch_allowed=False,
                integration_ready=False,
                blocking_reasons=persisted.reasons,
                unknown_kinds=readiness.unknown_kinds,
            )
        return _unknown_assessed_verdict(readiness.unknown_kinds)

    if persisted is not None:
        _log_persisted_override(persisted, readiness)
        _correct_persisted_record(state, readiness, write_back)

    return _AssessedIntegrationResolutionVerdict(
        RESOLVED,
        (),
        None,
        development_dispatch_allowed=True,
        integration_ready=readiness.integration_ready,
        blocking_reasons=readiness.blocking_reasons,
    )


@dataclass(frozen=True)
class _RetainedRecord:
    reason: str | None = None
    already_landed: bool = False


_RETAINED_RECEIPT_REASON = (
    "durable integration record retained; recovery must finish before ordinary dispatch"
)
_UNREADABLE_RECEIPT_REASON = "durable integration ownership unreadable; recovery required"


def _landing_already_happened(root: Path, record: IntegrationRecord | None) -> bool:
    if record is None or record.phase != "integrated" or not record.integrated_feature_sha:
        return False
    if record.owning_worktree is not None and Path(record.owning_worktree) != root:
        return False
    if record.diagnostic_ownership is not None or record.diagnostic_intervention_required:
        return False
    target_sha, query_ok = observe_branch_sha(root, record.target)
    return query_ok and target_sha == record.integrated_feature_sha


def _observe_retained_record(
    root: object,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> _RetainedRecord:
    from ralph.pipeline.auto_integrate_record import (
        RecordReadOutcomeDisposition,
        read_record_outcome,
        record_path,
    )

    if not isinstance(root, Path):
        return _RetainedRecord()
    try:
        outcome = read_record_outcome(
            root,
            backend=backend,
            busy_clock=time.monotonic,
        )
        if outcome.disposition in (
            RecordReadOutcomeDisposition.PRESENT,
            RecordReadOutcomeDisposition.UNREADABLE,
            RecordReadOutcomeDisposition.BUSY,
        ):
            return _RetainedRecord(
                reason=_RETAINED_RECEIPT_REASON,
                already_landed=_landing_already_happened(root, outcome.record),
            )
        record_path(root).lstat()
    except FileNotFoundError:
        return _RetainedRecord()
    except OSError:
        return _RetainedRecord(reason=_UNREADABLE_RECEIPT_REASON)
    return _RetainedRecord(reason=_RETAINED_RECEIPT_REASON)


def retained_integration_reason(
    root: object,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> str | None:
    return _observe_retained_record(root, backend=backend).reason


def unmerged_porcelain_paths(porcelain_output: str) -> tuple[str, ...]:
    unmerged: list[str] = []
    for raw_line in porcelain_output.splitlines():
        if len(raw_line) < _PORCELAIN_MIN_ENTRY_LEN:
            continue
        code = raw_line[:2]
        if code[0] == "U" or code[1] == "U" or code in _BOTH_SIDES_UNMERGED_CODES:
            path = raw_line[3:].strip() or raw_line.strip()
            unmerged.append(_decode_quoted_porcelain_path(path))
    return tuple(unmerged)


def persisted_integration_resolution_verdict(
    state: RebaseState,
) -> IntegrationResolutionVerdict | None:
    if state.resolution_exhausted:
        return IntegrationResolutionVerdict(
            EXHAUSTED,
            (state.resolution_exhaustion_reason or "conflict resolver exhausted",),
        )
    if state.integration_unresolved:
        return IntegrationResolutionVerdict(
            RECOVERABLE,
            ("persisted integration state is unresolved",),
            RESOLUTION_DRAIN,
        )
    return None


def assert_non_resolution_dispatch_allowed(
    phase: str,
    verdict: IntegrationResolutionVerdict,
) -> None:
    if phase != RESOLUTION_DRAIN and not verdict.dispatch_allowed:
        raise IntegrationDispatchBlockedError(phase, verdict)


def _full_porcelain(root: Path) -> tuple[bool, str]:
    result = run_git(
        (*COMMIT_PIN_CONFIG_ARGS, "status", "--porcelain", "--untracked-files=no"),
        cwd=root,
        label="git-integration-resolution-status",
    )
    return result.returncode == 0, result.stdout
