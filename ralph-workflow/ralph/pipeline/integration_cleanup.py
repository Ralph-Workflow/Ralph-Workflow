from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, cast

from loguru import logger

from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS, parse_porcelain_z
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.pipeline.auto_integrate_backup_refs import create_stale_resolution_snapshot
from ralph.pipeline.integration_decision import (
    AttemptState,
    DecisionContext,
    DirtAttribution,
    NextAction,
    decide,
)
from ralph.pipeline.integration_observation import (
    DirtShape,
    HeadState,
    IndexFlagDirt,
    InProgressKind,
    InProgressOwnership,
    IntegrationRecordState,
    Observation,
    Reading,
    TargetVsFeature,
    TargetVsRemote,
    WorktreeState,
    read_in_progress_kind,
    read_index_flag_dirt,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_PERMITTED_ARTIFACT_PREFIX: Final[str] = ".agent/"

_STATE_FILENAME: Final[str] = "planning_residue_cleanup.json"

_QUARANTINE_DIRNAME: Final[str] = "planning-residue-quarantine"

_PRESERVATION_REF_PREFIX: Final[str] = "refs/ralph-planning-residue/preserved-"

_STATE_VERSION: Final[int] = 1

_CLEANUP_LADDER_ACTIONS: frozenset[NextAction] = frozenset(
    {
        NextAction.SAVE_AND_RESTORE,
        NextAction.PRESERVE_VIA_TEMP_INDEX,
        NextAction.QUARANTINE_AND_RESTORE,
        NextAction.REBUILD_IN_FRESH_WORKTREE,
        NextAction.ADAPT_ENVIRONMENT_AND_RETRY,
        NextAction.LAST_RESORT_RECOVERY,
    }
)

_UNMERGED_XY: frozenset[str] = frozenset({"UU", "AA", "DD", "AU", "UA", "DU", "UD"})

_OPERATION_LABELS: Final[dict[InProgressKind, str]] = {
    InProgressKind.MERGE: "merge",
    InProgressKind.REBASE: "rebase",
    InProgressKind.CHERRY_PICK: "cherry-pick",
    InProgressKind.REVERT: "revert",
    InProgressKind.AM: "am",
    InProgressKind.SEQUENCER: "sequencer",
}

_DEGRADED_RUNGS: frozenset[NextAction] = frozenset(
    {
        NextAction.REBUILD_IN_FRESH_WORKTREE,
        NextAction.ADAPT_ENVIRONMENT_AND_RETRY,
        NextAction.LAST_RESORT_RECOVERY,
    }
)


@dataclass(frozen=True)
class PlanningBaseline:
    head_sha: str
    status_blob: str
    tracked_diff_hash: str
    fingerprint: str
    human_work_ref: str | None
    flag_hashes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PlanningResidue:
    fingerprint: str
    tracked_paths: tuple[str, ...]
    ambiguous_paths: tuple[str, ...]
    untracked_paths: tuple[str, ...]
    overwritten_untracked_paths: tuple[str, ...]
    unmerged_paths: tuple[str, ...]
    index_flag_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResidueCleanupOutcome:
    clean: bool = False
    unreadable: bool = False
    flag_probe_unknown: bool = False
    head_moved: bool = False
    unmerged: bool = False
    operation_blocked: bool = False
    operation_unknown: bool = False
    operation_kind: InProgressKind | None = None
    untracked_only: bool = False
    refused: bool = False
    resumed: bool = False
    degraded: bool = False
    action: NextAction | None = None
    backup_ref: str | None = None
    restored_paths: tuple[str, ...] = ()
    ambiguous_paths: tuple[str, ...] = ()
    quarantined_untracked: tuple[str, ...] = ()
    remaining_paths: tuple[str, ...] = ()


def _tracked_entries(status_blob: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for entry in parse_porcelain_z(status_blob):
        if entry.xy == "??" or entry.path.startswith(_PERMITTED_ARTIFACT_PREFIX):
            continue
        entries[entry.path] = entry.xy
    return entries


def _untracked_paths(status_blob: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            entry.path
            for entry in parse_porcelain_z(status_blob)
            if entry.xy == "??" and not entry.path.startswith(_PERMITTED_ARTIFACT_PREFIX)
        )
    )


def compute_fingerprint(
    *,
    head_sha: str,
    tracked_entries: Mapping[str, str],
    tracked_diff_hash: str,
    flag_paths: tuple[str, ...] = (),
) -> str:
    digest = hashlib.sha256()
    digest.update(head_sha.encode("utf-8"))
    digest.update(b"\0")
    for path in sorted(tracked_entries):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(tracked_entries[path].encode("utf-8"))
        digest.update(b"\0")
        digest.update(tracked_diff_hash.encode("utf-8"))
    for path in sorted(flag_paths):
        digest.update(b"\0flag\0")
        digest.update(path.encode("utf-8"))
    return digest.hexdigest()


def detect_planning_residue(
    baseline: PlanningBaseline,
    after_blob: str,
    *,
    after_diff_hash: str,
    incoming_overwrite_paths: frozenset[str] = frozenset(),
    after_flag_dirt: IndexFlagDirt | None = None,
) -> PlanningResidue | None:
    before_flag_hashes = dict(baseline.flag_hashes)
    after_flag_hashes = (
        after_flag_dirt.path_hashes if after_flag_dirt is not None else baseline.flag_hashes
    )
    index_flag_paths = tuple(
        sorted(path for path, digest in after_flag_hashes if before_flag_hashes.get(path) != digest)
    )
    if (
        after_blob == baseline.status_blob
        and after_diff_hash == baseline.tracked_diff_hash
        and not index_flag_paths
    ):
        return None
    before_tracked = _tracked_entries(baseline.status_blob)
    after_tracked = _tracked_entries(after_blob)
    before_untracked = frozenset(_untracked_paths(baseline.status_blob))
    after_untracked = frozenset(_untracked_paths(after_blob))

    tracked: list[str] = []
    ambiguous: list[str] = []
    for path, after_xy in sorted(after_tracked.items()):
        before_xy = before_tracked.get(path)
        if before_xy is None:
            tracked.append(path)
        elif before_xy != after_xy:
            ambiguous.append(path)
    ambiguous.extend(
        path
        for path in sorted(before_tracked)
        if path not in after_tracked and path not in after_untracked
    )

    if after_diff_hash != baseline.tracked_diff_hash and not tracked and not ambiguous:
        ambiguous.extend(sorted(after_tracked))

    new_untracked = tuple(sorted(after_untracked - before_untracked))
    overwritten = tuple(sorted((before_untracked | after_untracked) & incoming_overwrite_paths))
    unmerged = tuple(
        sorted(
            entry.path
            for entry in parse_porcelain_z(after_blob)
            if entry.xy in _UNMERGED_XY and not entry.path.startswith(_PERMITTED_ARTIFACT_PREFIX)
        )
    )
    if not (tracked or ambiguous or new_untracked or overwritten or unmerged or index_flag_paths):
        return None
    fingerprint = compute_fingerprint(
        head_sha=baseline.head_sha,
        tracked_entries=after_tracked,
        tracked_diff_hash=after_diff_hash,
        flag_paths=index_flag_paths,
    )
    return PlanningResidue(
        fingerprint=fingerprint,
        tracked_paths=tuple(tracked),
        ambiguous_paths=tuple(ambiguous),
        untracked_paths=new_untracked,
        overwritten_untracked_paths=overwritten,
        unmerged_paths=unmerged,
        index_flag_paths=index_flag_paths,
    )


def residue_observation(residue: PlanningResidue) -> tuple[Observation, DirtShape]:
    if residue.unmerged_paths:
        worktree = WorktreeState.UNMERGED
        shape = DirtShape.UNMERGED
    elif residue.tracked_paths or residue.ambiguous_paths:
        worktree = WorktreeState.TRACKED_CHANGES
        shape = DirtShape.UNSTAGED_MODIFICATION
    elif residue.index_flag_paths:
        worktree = WorktreeState.TRACKED_CHANGES
        shape = DirtShape.INDEX_FLAG_HIDDEN
    else:
        worktree = WorktreeState.UNTRACKED
        shape = (
            DirtShape.UNTRACKED_BLOCKING
            if residue.overwritten_untracked_paths
            else DirtShape.UNTRACKED_ONLY
        )
    return Observation(
        target_vs_feature=TargetVsFeature.EQUAL,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=worktree,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.NONE,
        agent_active=False,
    ), shape


def residue_attribution(residue: PlanningResidue) -> DirtAttribution:
    if residue.ambiguous_paths:
        return DirtAttribution.UNKNOWN
    return DirtAttribution.UNOWNED


class CleanupGitPort(Protocol):
    def porcelain_status_z(self, root: Path) -> str | None: ...

    def tracked_diff_hash(self, root: Path) -> str | None: ...

    def head_sha(self, root: Path) -> str | None: ...

    def in_progress_kind(self, root: Path) -> Reading[InProgressKind]: ...

    def index_flag_dirt(self, root: Path) -> Reading[IndexFlagDirt]: ...

    def clear_index_flags(self, root: Path, paths: Sequence[str]) -> bool: ...

    def snapshot_worktree(self, root: Path, *, head_sha: str) -> str: ...

    def ref_exists(self, root: Path, ref: str) -> bool: ...

    def restore_paths(self, root: Path, *, source: str, paths: Sequence[str]) -> bool: ...

    def preserve_dirt_commit(
        self, root: Path, *, head_sha: str, paths: Sequence[str]
    ) -> str | None: ...

    def quarantine_paths(
        self,
        root: Path,
        paths: Sequence[str],
        *,
        quarantine_dir: Path,
    ) -> tuple[str, ...]: ...

    def incoming_overwrite_paths(self, root: Path, target: str | None) -> frozenset[str]: ...

    def load_state(self, root: Path) -> Mapping[str, object] | None: ...

    def store_state(self, root: Path, payload: Mapping[str, object]) -> None: ...

    def clear_state(self, root: Path) -> None: ...

    def path_exists(self, path: Path) -> bool: ...

    def move_path(self, source: str, destination: Path) -> None: ...


class ResidueLedgerPort(Protocol):
    def charge_residue_attempt(self, root: Path, *, residue: PlanningResidue) -> NextAction: ...


@dataclass
class _CleanupProgress:
    fingerprint: str = ""
    head_sha: str = ""
    backup_ref: str | None = None
    restore_paths: list[str] = field(default_factory=list)
    restored_paths: list[str] = field(default_factory=list)
    ambiguous_paths: list[str] = field(default_factory=list)
    quarantined_untracked: dict[str, str] = field(default_factory=dict)
    completed: bool = False

    def to_payload(self) -> dict[str, object]:
        return {
            "version": _STATE_VERSION,
            "fingerprint": self.fingerprint,
            "head_sha": self.head_sha,
            "backup_ref": self.backup_ref,
            "restore_paths": list(self.restore_paths),
            "restored_paths": list(self.restored_paths),
            "ambiguous_paths": list(self.ambiguous_paths),
            "quarantined_untracked": dict(self.quarantined_untracked),
            "completed": self.completed,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> _CleanupProgress | None:
        if payload.get("version") != _STATE_VERSION:
            return None
        fingerprint = payload.get("fingerprint")
        head_sha = payload.get("head_sha")
        if not isinstance(fingerprint, str) or not isinstance(head_sha, str):
            return None
        backup_ref = payload.get("backup_ref")
        return cls(
            fingerprint=fingerprint,
            head_sha=head_sha,
            backup_ref=backup_ref if isinstance(backup_ref, str) else None,
            restore_paths=_str_list(payload.get("restore_paths")),
            restored_paths=_str_list(payload.get("restored_paths")),
            ambiguous_paths=_str_list(payload.get("ambiguous_paths")),
            quarantined_untracked=_str_dict(payload.get("quarantined_untracked")),
            completed=bool(payload.get("completed", False)),
        )


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _str_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if isinstance(item, str)}


def capture_planning_baseline(
    root: Path,
    *,
    git: CleanupGitPort,
) -> PlanningBaseline | None:
    blob = git.porcelain_status_z(root)
    diff_hash = git.tracked_diff_hash(root)
    head = git.head_sha(root)
    flag_reading = git.index_flag_dirt(root)
    if blob is None or diff_hash is None or head is None or not flag_reading.is_known_good:
        return None
    flag_dirt = flag_reading.value if flag_reading.value is not None else IndexFlagDirt.empty()
    tracked = _tracked_entries(blob)
    human_work_ref: str | None = None
    if tracked:
        human_work_ref = git.snapshot_worktree(root, head_sha=head)
    fingerprint = compute_fingerprint(
        head_sha=head, tracked_entries=tracked, tracked_diff_hash=diff_hash
    )
    return PlanningBaseline(
        head_sha=head,
        status_blob=blob,
        tracked_diff_hash=diff_hash,
        fingerprint=fingerprint,
        human_work_ref=human_work_ref,
        flag_hashes=flag_dirt.path_hashes,
    )


def run_residue_cleanup(
    root: Path,
    *,
    baseline: PlanningBaseline,
    git: CleanupGitPort,
    ledger: ResidueLedgerPort,
    target: str | None = None,
    warn: Callable[[str], None] | None = None,
) -> ResidueCleanupOutcome:
    emit = warn if warn is not None else _default_warn
    try:
        return _run_residue_cleanup(
            root, baseline=baseline, git=git, ledger=ledger, target=target, emit=emit
        )
    except Exception as exc:
        logger.warning("planning-residue cleanup raised; leaving tree untouched: {}", exc)
        return ResidueCleanupOutcome(refused=True)


def _default_warn(message: str) -> None:
    logger.warning("{}", message)


def _run_residue_cleanup(
    root: Path,
    *,
    baseline: PlanningBaseline,
    git: CleanupGitPort,
    ledger: ResidueLedgerPort,
    target: str | None,
    emit: Callable[[str], None],
) -> ResidueCleanupOutcome:
    after_blob = git.porcelain_status_z(root)
    after_diff = git.tracked_diff_hash(root)
    head = git.head_sha(root)
    if after_blob is None or after_diff is None or head is None:
        emit("planning residue check unreadable; tree left untouched")
        return ResidueCleanupOutcome(unreadable=True)
    flags = git.index_flag_dirt(root)
    if not flags.is_known_good:
        emit(
            "index-flag probe unreadable; cleanup backing off rather than "
            "certifying hidden flagged dirt clean (tree left untouched)"
        )
        return ResidueCleanupOutcome(flag_probe_unknown=True)
    after_flag_dirt = flags.require_known()
    gated = _operation_gate(root, git, emit)
    if gated is not None:
        return gated
    return _classify_and_clear_residue(
        root,
        baseline=baseline,
        git=git,
        ledger=ledger,
        target=target,
        after_blob=after_blob,
        after_diff=after_diff,
        after_flag_dirt=after_flag_dirt,
        head=head,
        emit=emit,
    )


def _classify_and_clear_residue(
    root: Path,
    *,
    baseline: PlanningBaseline,
    git: CleanupGitPort,
    ledger: ResidueLedgerPort,
    target: str | None,
    after_blob: str,
    after_diff: str,
    after_flag_dirt: IndexFlagDirt,
    head: str,
    emit: Callable[[str], None],
) -> ResidueCleanupOutcome:
    if head != baseline.head_sha:
        emit(
            "planning left residue but HEAD moved "
            f"({baseline.head_sha[:8]} -> {head[:8]}); restore refused"
        )
        return ResidueCleanupOutcome(head_moved=True)
    residue = detect_planning_residue(
        baseline,
        after_blob,
        after_diff_hash=after_diff,
        incoming_overwrite_paths=git.incoming_overwrite_paths(root, target),
        after_flag_dirt=after_flag_dirt,
    )
    if residue is None:
        _retire_state_when_done(root, git)
        return ResidueCleanupOutcome(clean=True)
    if residue.unmerged_paths:
        emit(
            "planning left unmerged conflict state; cleanup ladder refuses "
            f"to touch it: {', '.join(residue.unmerged_paths)}"
        )
        return ResidueCleanupOutcome(unmerged=True)
    if not (
        residue.tracked_paths
        or residue.ambiguous_paths
        or residue.overwritten_untracked_paths
        or residue.index_flag_paths
    ):
        return ResidueCleanupOutcome(untracked_only=True)
    return _cleanup_blocking_residue(root, baseline, git, ledger, residue, emit)


def _operation_gate(
    root: Path,
    git: CleanupGitPort,
    emit: Callable[[str], None],
) -> ResidueCleanupOutcome | None:
    in_progress = git.in_progress_kind(root)
    if not in_progress.is_known_good:
        emit(
            "operation-state probe unreadable; cleanup refusing to act "
            "on an unknown operation state (tree left untouched)"
        )
        return ResidueCleanupOutcome(operation_unknown=True)
    kind = in_progress.require_known()
    if kind is InProgressKind.NONE:
        return None
    emit(
        f"git {_OPERATION_LABELS[kind]} in progress; the operation owns "
        "staged changes until continue/abort/rebuild — planning-residue "
        "cleanup takes no restore/reset action"
    )
    return ResidueCleanupOutcome(operation_blocked=True, operation_kind=kind)


def _cleanup_blocking_residue(
    root: Path,
    baseline: PlanningBaseline,
    git: CleanupGitPort,
    ledger: ResidueLedgerPort,
    residue: PlanningResidue,
    emit: Callable[[str], None],
) -> ResidueCleanupOutcome:
    action = ledger.charge_residue_attempt(root, residue=residue)
    if action not in _CLEANUP_LADDER_ACTIONS:
        emit(
            f"decision table returned non-cleanup action {action.value} for "
            "planning residue; refusing to run it (residue is never committed "
            "as feature work)"
        )
        return ResidueCleanupOutcome(refused=True)

    if residue.index_flag_paths and not git.clear_index_flags(root, residue.index_flag_paths):
        emit(
            "index flags on "
            f"{', '.join(residue.index_flag_paths)} could not be cleared; "
            "cleanup refusing to restore content the backup snapshot "
            "cannot see (tree left untouched)"
        )
        return ResidueCleanupOutcome(refused=True)

    progress = _resume_or_start_progress(root, git, baseline, residue)
    resumed = progress.backup_ref is not None
    _ensure_backup_ref(root, git, baseline, progress)
    moved_untracked = _quarantine_overwrite_blockers(root, git, progress, residue)
    restore_targets = _execute_rung_and_restore(root, git, baseline, action, progress, emit)
    remaining, restored = _finalize_progress(root, git, baseline, progress, restore_targets)
    _report_residue_cleanup(
        emit,
        action=action,
        backup_ref=progress.backup_ref,
        residue=residue,
        baseline=baseline,
        moved_untracked=moved_untracked,
        restored=restored,
        remaining=remaining,
    )
    return ResidueCleanupOutcome(
        resumed=resumed,
        degraded=action in _DEGRADED_RUNGS,
        action=action,
        backup_ref=progress.backup_ref,
        restored_paths=restored,
        ambiguous_paths=residue.ambiguous_paths,
        quarantined_untracked=tuple(sorted(progress.quarantined_untracked)),
        remaining_paths=remaining,
    )


def _ensure_backup_ref(
    root: Path,
    git: CleanupGitPort,
    baseline: PlanningBaseline,
    progress: _CleanupProgress,
) -> None:
    if progress.backup_ref is None or not git.ref_exists(root, progress.backup_ref):
        progress.backup_ref = git.snapshot_worktree(root, head_sha=baseline.head_sha)
        progress.restored_paths = []


def _quarantine_overwrite_blockers(
    root: Path,
    git: CleanupGitPort,
    progress: _CleanupProgress,
    residue: PlanningResidue,
) -> tuple[str, ...]:
    unmoved = tuple(
        path
        for path in residue.overwritten_untracked_paths
        if path not in progress.quarantined_untracked
    )
    if not unmoved:
        return ()
    quarantine_dir = _quarantine_dir(root)
    moved = git.quarantine_paths(root, unmoved, quarantine_dir=quarantine_dir)
    for path in moved:
        progress.quarantined_untracked[path] = str(quarantine_dir / path)
    return moved


def _execute_rung_and_restore(
    root: Path,
    git: CleanupGitPort,
    baseline: PlanningBaseline,
    action: NextAction,
    progress: _CleanupProgress,
    emit: Callable[[str], None],
) -> list[str]:
    restore_targets = [
        path for path in progress.restore_paths if path not in progress.restored_paths
    ]
    if not restore_targets:
        return []
    _perform_action(root, git, baseline, action, restore_targets, emit)
    if not git.restore_paths(root, source=baseline.head_sha, paths=restore_targets):
        emit(
            "planning-residue restore failed (backup ref "
            f"{progress.backup_ref} still holds the saved content)"
        )
    return restore_targets


def _finalize_progress(
    root: Path,
    git: CleanupGitPort,
    baseline: PlanningBaseline,
    progress: _CleanupProgress,
    restore_targets: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    final_blob = git.porcelain_status_z(root)
    final_diff = git.tracked_diff_hash(root)
    final_flags = git.index_flag_dirt(root)
    if final_blob is None or final_diff is None or not final_flags.is_known_good:
        still_dirty = set(restore_targets)
    else:
        final_residue = detect_planning_residue(
            baseline,
            final_blob,
            after_diff_hash=final_diff,
            after_flag_dirt=final_flags.require_known(),
        )
        still_dirty = (
            set(final_residue.tracked_paths) | set(final_residue.index_flag_paths)
            if final_residue is not None
            else set()
        )
    progress.restored_paths = [path for path in progress.restore_paths if path not in still_dirty]
    progress.completed = not still_dirty
    git.store_state(root, progress.to_payload())
    if progress.completed and not progress.quarantined_untracked:
        git.clear_state(root)
    remaining = tuple(sorted(still_dirty))
    restored = tuple(path for path in progress.restored_paths if path in restore_targets)
    return remaining, restored


def _report_residue_cleanup(
    emit: Callable[[str], None],
    *,
    action: NextAction,
    backup_ref: str | None,
    residue: PlanningResidue,
    baseline: PlanningBaseline,
    moved_untracked: tuple[str, ...],
    restored: tuple[str, ...],
    remaining: tuple[str, ...],
) -> None:
    message_parts = [
        f"planning residue cleanup ({action.value})",
        f"restored: {', '.join(restored) if restored else 'none'}",
        f"backup ref: {backup_ref}",
    ]
    if residue.ambiguous_paths:
        human_ref = baseline.human_work_ref or backup_ref
        message_parts.append(
            "pre-existing human work preserved, NOT reverted: "
            f"{', '.join(residue.ambiguous_paths)} (ref: {human_ref})"
        )
    if residue.index_flag_paths:
        message_parts.append(
            "index-flag-hidden dirt (assume-unchanged/skip-worktree) unhidden, "
            f"saved and restored: {', '.join(residue.index_flag_paths)}"
        )
    if moved_untracked:
        message_parts.append(
            f"untracked overwrite-blockers moved aside: {', '.join(moved_untracked)} "
            "(restore after landing via restore_quarantined_untracked)"
        )
    if action in _DEGRADED_RUNGS:
        message_parts.append(
            f"rung {action.value} needs machinery owned elsewhere; executed at quarantine strength"
        )
    if remaining:
        message_parts.append(f"still dirty after restore: {', '.join(remaining)}")
    emit("; ".join(message_parts))


def _perform_action(
    root: Path,
    git: CleanupGitPort,
    baseline: PlanningBaseline,
    action: NextAction,
    restore_targets: Sequence[str],
    emit: Callable[[str], None],
) -> None:
    if action is NextAction.SAVE_AND_RESTORE:
        return
    if action is NextAction.PRESERVE_VIA_TEMP_INDEX:
        try:
            preserved = git.preserve_dirt_commit(
                root, head_sha=baseline.head_sha, paths=restore_targets
            )
        except Exception as exc:
            emit(f"alternate preservation failed ({exc}); continuing with restore")
            return
        if preserved is not None:
            emit(f"alternate preservation ref: {preserved}")
        return
    quarantine_dir = _quarantine_dir(root)
    moved = git.quarantine_paths(root, restore_targets, quarantine_dir=quarantine_dir)
    if moved:
        emit(f"quarantined residue files: {', '.join(moved)}")


def _resume_or_start_progress(
    root: Path,
    git: CleanupGitPort,
    baseline: PlanningBaseline,
    residue: PlanningResidue,
) -> _CleanupProgress:
    carried_quarantine: dict[str, str] = {}
    existing = git.load_state(root)
    if existing is not None:
        prior = _CleanupProgress.from_payload(existing)
        if prior is not None:
            carried_quarantine = dict(prior.quarantined_untracked)
            if prior.fingerprint == residue.fingerprint and prior.head_sha == baseline.head_sha:
                return prior
    return _CleanupProgress(
        fingerprint=residue.fingerprint,
        head_sha=baseline.head_sha,
        restore_paths=[*residue.tracked_paths, *residue.index_flag_paths],
        ambiguous_paths=list(residue.ambiguous_paths),
        quarantined_untracked=carried_quarantine,
    )


def _retire_state_when_done(root: Path, git: CleanupGitPort) -> None:
    existing = git.load_state(root)
    if existing is None:
        return
    progress = _CleanupProgress.from_payload(existing)
    if progress is None or progress.quarantined_untracked:
        return
    git.clear_state(root)


def _quarantine_dir(root: Path) -> Path:
    return root / ".agent" / _QUARANTINE_DIRNAME


def restore_quarantined_untracked(
    root: Path,
    *,
    git: CleanupGitPort,
    emit: Callable[[str], None] | None = None,
) -> tuple[str, ...]:
    log = emit if emit is not None else _default_warn
    existing = git.load_state(root)
    if existing is None:
        return ()
    progress = _CleanupProgress.from_payload(existing)
    if progress is None or not progress.quarantined_untracked:
        return ()
    restored: list[str] = []
    still_quarantined: dict[str, str] = {}
    for path, stored in sorted(progress.quarantined_untracked.items()):
        destination = root / path
        try:
            if git.path_exists(destination):
                still_quarantined[path] = stored
                continue
            git.move_path(stored, destination)
            restored.append(path)
        except OSError as exc:
            logger.warning("planning-residue: could not restore {}: {}", path, exc)
            still_quarantined[path] = stored
    progress.quarantined_untracked = still_quarantined
    if still_quarantined:
        git.store_state(root, progress.to_payload())
    else:
        git.clear_state(root)
    if restored:
        log(f"restored quarantined untracked files after landing: {', '.join(restored)}")
    return tuple(restored)


class RunGitCleanupPort:
    def porcelain_status_z(self, root: Path) -> str | None:
        result = run_git(
            ("status", "--porcelain", "-z"),
            cwd=root,
            label="planning-residue:status-z",
        )
        if result.returncode != 0:
            return None
        return result.stdout

    def tracked_diff_hash(self, root: Path) -> str | None:
        result = run_git(
            ("diff", "--no-renames", "HEAD"),
            cwd=root,
            label="planning-residue:tracked-diff",
        )
        if result.returncode != 0:
            return None
        return hashlib.sha256(result.stdout.encode("utf-8", errors="surrogateescape")).hexdigest()

    def head_sha(self, root: Path) -> str | None:
        result = run_git(
            ("rev-parse", "--verify", "HEAD"),
            cwd=root,
            label="planning-residue:head",
        )
        sha = result.stdout.strip()
        if result.returncode != 0 or not sha:
            return None
        return sha

    def in_progress_kind(self, root: Path) -> Reading[InProgressKind]:
        return read_in_progress_kind(root)

    def index_flag_dirt(self, root: Path) -> Reading[IndexFlagDirt]:
        return read_index_flag_dirt(root)

    def clear_index_flags(self, root: Path, paths: Sequence[str]) -> bool:
        if not paths:
            return True
        result = run_git(
            ("update-index", "--no-assume-unchanged", "--no-skip-worktree", "--", *paths),
            cwd=root,
            label="planning-residue:clear-index-flags",
        )
        return result.returncode == 0

    def snapshot_worktree(self, root: Path, *, head_sha: str) -> str:
        backup_ref, _feature_sha = create_stale_resolution_snapshot(
            root,
            feature_name=None,
            head_sha=head_sha,
        )
        return backup_ref

    def ref_exists(self, root: Path, ref: str) -> bool:
        result = run_git(
            ("rev-parse", "--verify", "--quiet", ref),
            cwd=root,
            label="planning-residue:ref-exists",
        )
        return result.returncode == 0

    def restore_paths(self, root: Path, *, source: str, paths: Sequence[str]) -> bool:
        if not paths:
            return True
        result = run_git(
            [
                *COMMIT_PIN_CONFIG_ARGS,
                "restore",
                "--worktree",
                "--staged",
                f"--source={source}",
                "--",
                *paths,
            ],
            cwd=root,
            label="planning-residue:restore",
        )
        return result.returncode == 0

    def preserve_dirt_commit(
        self, root: Path, *, head_sha: str, paths: Sequence[str]
    ) -> str | None:
        del paths
        created = run_git(
            ("stash", "create", "ralph planning-residue preservation"),
            cwd=root,
            label="planning-residue:preserve-stash-create",
        )
        if created.returncode != 0:
            stderr = (created.stderr or created.stdout).strip()[:200]
            raise RuntimeError(f"planning-residue preservation read failed: {stderr}")
        stash_sha = created.stdout.strip()
        if not stash_sha:
            return None
        tree = run_git(
            ("rev-parse", "--verify", f"{stash_sha}^{{tree}}"),
            cwd=root,
            label="planning-residue:preserve-tree",
        )
        tree_id = tree.stdout.strip()
        if tree.returncode != 0 or not tree_id:
            stderr = (tree.stderr or tree.stdout).strip()[:200]
            raise RuntimeError(f"planning-residue preservation tree unreadable: {stderr}")
        commit = run_git(
            [
                *COMMIT_PIN_CONFIG_ARGS,
                "commit-tree",
                tree_id,
                "-p",
                head_sha,
            ],
            cwd=root,
            label="planning-residue:preserve-commit",
            options=GitRunOptions(
                env={
                    "GIT_AUTHOR_NAME": "Ralph Workflow",
                    "GIT_AUTHOR_EMAIL": "noreply@ralphworkflow.com",
                    "GIT_COMMITTER_NAME": "Ralph Workflow",
                    "GIT_COMMITTER_EMAIL": "noreply@ralphworkflow.com",
                },
                input_data="ralph planning-residue preservation\n",
            ),
        )
        commit_sha = commit.stdout.strip()
        if commit.returncode != 0 or not commit_sha:
            stderr = (commit.stderr or commit.stdout).strip()[:200]
            raise RuntimeError(f"planning-residue preservation commit failed: {stderr}")
        ref = f"{_PRESERVATION_REF_PREFIX}{commit_sha[:12]}"
        stored = run_git(
            ("update-ref", ref, commit_sha),
            cwd=root,
            label="planning-residue:preserve-ref",
        )
        if stored.returncode != 0:
            stderr = (stored.stderr or stored.stdout).strip()[:200]
            raise RuntimeError(f"planning-residue preservation ref failed: {stderr}")
        return ref

    def quarantine_paths(
        self,
        root: Path,
        paths: Sequence[str],
        *,
        quarantine_dir: Path,
    ) -> tuple[str, ...]:
        if not paths:
            return ()
        backend = DEFAULT_FILE_BACKEND
        backend.mkdir(quarantine_dir, parents=True, exist_ok=True)
        moved: list[str] = []
        for path in paths:
            source = root / path
            destination = quarantine_dir / path
            try:
                backend.mkdir(destination.parent, parents=True, exist_ok=True)
                backend.replace(source, destination)
            except OSError:
                continue
            moved.append(path)
        return tuple(moved)

    def incoming_overwrite_paths(self, root: Path, target: str | None) -> frozenset[str]:
        if not target:
            return frozenset()
        result = run_git(
            ("diff", "--name-only", "-z", "--diff-filter=ACMRT", "HEAD", target),
            cwd=root,
            label="planning-residue:incoming-overwrite",
        )
        if result.returncode != 0:
            return frozenset()
        return frozenset(
            path
            for path in result.stdout.split("\0")
            if path and not path.startswith(_PERMITTED_ARTIFACT_PREFIX)
        )

    def load_state(self, root: Path) -> Mapping[str, object] | None:
        path = root / ".agent" / _STATE_FILENAME
        if not DEFAULT_FILE_BACKEND.exists(path):
            return None
        try:
            parsed = cast("object", json.loads(DEFAULT_FILE_BACKEND.read_text(path)))
        except (OSError, ValueError):
            return None
        if not isinstance(parsed, Mapping):
            return None
        return parsed

    def store_state(self, root: Path, payload: Mapping[str, object]) -> None:
        path = root / ".agent" / _STATE_FILENAME
        DEFAULT_FILE_BACKEND.mkdir(path.parent, parents=True, exist_ok=True)
        write_text_if_changed(
            DEFAULT_FILE_BACKEND,
            path,
            json.dumps(payload, sort_keys=True),
            encoding="utf-8",
        )

    def clear_state(self, root: Path) -> None:
        DEFAULT_FILE_BACKEND.unlink(root / ".agent" / _STATE_FILENAME, missing_ok=True)

    def path_exists(self, path: Path) -> bool:
        return DEFAULT_FILE_BACKEND.exists(path)

    def move_path(self, source: str, destination: Path) -> None:
        backend = DEFAULT_FILE_BACKEND
        backend.mkdir(destination.parent, parents=True, exist_ok=True)
        backend.replace(Path(source), destination)


class OwnerResidueLedgerPort:
    def charge_residue_attempt(self, root: Path, *, residue: PlanningResidue) -> NextAction:
        from ralph.pipeline.integration_owner import (
            charge_integration_attempt,
            load_integration_attempts,
        )

        observation, shape = residue_observation(residue)
        attempts: AttemptState = load_integration_attempts(root)
        action = decide(
            DecisionContext(
                observation=observation,
                attempts=attempts,
                dirt_shape=shape,
                dirt_attribution=residue_attribution(residue),
            )
        )
        charge_integration_attempt(root, observation=observation, next_action=action)
        return action


__all__ = [
    "CleanupGitPort",
    "OwnerResidueLedgerPort",
    "PlanningBaseline",
    "PlanningResidue",
    "ResidueCleanupOutcome",
    "ResidueLedgerPort",
    "RunGitCleanupPort",
    "capture_planning_baseline",
    "compute_fingerprint",
    "detect_planning_residue",
    "residue_attribution",
    "residue_observation",
    "restore_quarantined_untracked",
    "run_residue_cleanup",
]
