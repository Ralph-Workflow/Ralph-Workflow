"""Planning-residue cleanup executor (AC-10 dirty-tree machinery).

The integration decision table (:mod:`ralph.pipeline.integration_decision`)
maps blocking dirt to the cleanup ladder — ``SAVE_AND_RESTORE`` for residue
and unknown attribution, climbing one rung per charged repeat. This module
is the impure machinery behind that decision for one specific seam: the
planning-agent dispatch.

Planning is read-only for source files. Its only permitted writes are its
own artifacts under ``.agent/`` and git-ignored paths. The executor enforces
that contract mechanically:

* :func:`capture_planning_baseline` records the pre-dispatch baseline —
  HEAD sha, the raw ``git status --porcelain -z`` blob (untracked included),
  a tracked-diff hash, and a content fingerprint — before every planning
  dispatch. When the baseline tree already carries tracked dirt (pre-existing
  human work), the baseline also snapshots that work under a durable backup
  ref so later ambiguity can never silently revert it.
* :func:`run_residue_cleanup` re-observes after the dispatch and diffs the
  two views (pure, in :func:`detect_planning_residue`). Tracked paths that
  were clean at baseline and dirty after the dispatch are planning residue:
  they are saved to a backup ref (the shared
  :mod:`ralph.pipeline.auto_integrate_backup_refs` machinery — refs, never
  ``git stash``, because refs are reachable from every worktree), restored
  to the baseline, and named in exactly one WARNING together with the
  backup ref. Paths that were already dirty at baseline AND changed during
  the dispatch are attribution-ambiguous: they are treated as pre-existing
  human work — saved, never silently reverted, and reported. Untracked-only
  residue does not block. Untracked files the incoming target commits would
  overwrite are blocking: they are moved aside into a quarantine directory
  under ``.agent/`` and restored by the   landing seam via
  :func:`restore_quarantined_untracked`. Unmerged (conflict) state created
  during planning is NEVER touched by cleanup.

  An in-progress git operation blocks cleanup entirely: the
  operation-marker probe (``MERGE_HEAD``, ``rebase-merge/``,
  ``rebase-apply/``, ``CHERRY_PICK_HEAD`` via
  :func:`~ralph.pipeline.integration_observation.read_in_progress_kind`)
  runs before any restore, and ANY marker defers the run with a typed
  outcome — the operation owns the index and its staged resolution until
  the operation ladder (continue/abort/rebuild) resolves it. An unreadable
  probe is UNKNOWN, never "clean/none".

Every mutation is bounded and ledgered: the executor charges the
repository-wide attempt ledger (intent before action) through
:meth:`ResidueLedgerPort.charge_residue_attempt`, which delegates to the
decision table so a repeat failure is answered with the next cleanup-ladder
rung, never a re-run of a failed action and never a human hand-off.

Cleanup is resumable. Progress (backup ref, restore set, restored subset,
quarantined untracked files) is recorded under ``.agent/``; an interruption
between the backup ref and the restore resumes on the next event without a
second backup of the same content — the resumed run skips the snapshot when
the recorded backup ref still exists and restores only the paths that are
still dirty (compare-and-act on a fresh observation). Cleanup re-observes
before acting: a moved HEAD aborts the restore rather than rewriting files
against a baseline that no longer describes the tree.

All Git goes through :func:`ralph.git.subprocess_runner.run_git`, which
serializes every call through the per-common-dir command queue and applies
the non-interactive hardening. Every git invocation here is plumbing-class
(``status``/``diff`` reads, ``stash create`` / ``commit-tree`` /
``update-ref`` / ``write-tree`` preservation, ``restore`` for the
worktree/index), which runs no hooks by construction; the fixed
:data:`~ralph.git.hardening.COMMIT_PIN_CONFIG_ARGS` pins accompany the
commit-plumbing calls. There are no waits: nothing sleeps, so no clock is
needed.
"""

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
    InProgressKind,
    InProgressOwnership,
    IntegrationRecordState,
    Observation,
    Reading,
    TargetVsFeature,
    TargetVsRemote,
    WorktreeState,
    read_in_progress_kind,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

#: Permitted planning writes live under this prefix; they are excluded from
#: residue detection so a planning agent's own artifacts never count as dirt.
_PERMITTED_ARTIFACT_PREFIX: Final[str] = ".agent/"

#: Durable cleanup-progress file, under ``.agent/`` (runtime state, never tracked).
_STATE_FILENAME: Final[str] = "planning_residue_cleanup.json"

#: Directory (under ``.agent/``) untracked overwrite-blockers are quarantined into.
_QUARANTINE_DIRNAME: Final[str] = "planning-residue-quarantine"

#: Ref namespace for the alternate-preservation rung's preserved commit.
_PRESERVATION_REF_PREFIX: Final[str] = "refs/ralph-planning-residue/preserved-"

#: Version of the on-disk cleanup-state payload.
_STATE_VERSION: Final[int] = 1

#: The cleanup-ladder actions the executor knows how to perform. A decision
#: outside this set is refused (logged, no tree mutation): residue is never
#: committed as feature work and never handed to a human.
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

#: Unmerged porcelain codes: a conflict state the cleanup ladder must not touch.
_UNMERGED_XY: frozenset[str] = frozenset({"UU", "AA", "DD", "AU", "UA", "DU", "UD"})

#: Human-readable labels for every operation state that blocks cleanup.
#: Explicit total map over the non-``NONE`` :class:`InProgressKind`
#: vocabulary (not ``kind.value``): ``InProgressKind`` is a plain Enum, so
#: its ``.value`` is dynamically typed and the repo's mypy config disallows
#: Any expressions. The label must stay total: a ``KeyError`` here would be
#: caught by the executor's defensive wrapper and mask the typed outcome.
_OPERATION_LABELS: Final[dict[InProgressKind, str]] = {
    InProgressKind.MERGE: "merge",
    InProgressKind.REBASE: "rebase",
    InProgressKind.CHERRY_PICK: "cherry-pick",
    InProgressKind.REVERT: "revert",
    InProgressKind.AM: "am",
    InProgressKind.SEQUENCER: "sequencer",
}

#: A degraded rung is executed at quarantine strength; the outcome names the
#: rung the ladder actually selected so the report is honest about it.
_DEGRADED_RUNGS: frozenset[NextAction] = frozenset(
    {
        NextAction.REBUILD_IN_FRESH_WORKTREE,
        NextAction.ADAPT_ENVIRONMENT_AND_RETRY,
        NextAction.LAST_RESORT_RECOVERY,
    }
)


# ---------------------------------------------------------------------------
# Pure model: baseline, residue, outcome.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanningBaseline:
    """Pre-planning-dispatch tree snapshot used as the restore target.

    ``status_blob`` is the raw ``git status --porcelain -z`` output
    (untracked included, permitted ``.agent/`` artifacts still present —
    filtering happens in the pure comparators so the blob stays a faithful
    observation). ``tracked_diff_hash`` fingerprints ``git diff HEAD`` so a
    content-only edit that leaves the porcelain code unchanged is still
    detected. ``human_work_ref`` is the backup ref holding the pre-dispatch
    tree when the baseline already carried tracked dirt; ``None`` when the
    baseline tracked tree was clean.
    """

    head_sha: str
    status_blob: str
    tracked_diff_hash: str
    fingerprint: str
    human_work_ref: str | None


@dataclass(frozen=True)
class PlanningResidue:
    """Diff between the pre-dispatch baseline and the post-dispatch tree.

    * ``tracked_paths`` — clean at baseline, dirty after the dispatch:
      unambiguous planning residue. Saved and restored.
    * ``ambiguous_paths`` — dirty at baseline AND changed during the
      dispatch: attribution cannot separate planning-agent edits from human
      edits, so the path is treated as pre-existing human work — saved,
      never silently reverted, reported.
    * ``untracked_paths`` — new untracked files outside ``.agent/``:
      non-blocking; planning continues with them in place.
    * ``overwritten_untracked_paths`` — untracked files the incoming target
      commits would overwrite: blocking; moved aside and restored after
      landing.
    * ``unmerged_paths`` — conflict state created during the dispatch:
      cleanup never touches these.
    """

    fingerprint: str
    tracked_paths: tuple[str, ...]
    ambiguous_paths: tuple[str, ...]
    untracked_paths: tuple[str, ...]
    overwritten_untracked_paths: tuple[str, ...]
    unmerged_paths: tuple[str, ...]


@dataclass(frozen=True)
class ResidueCleanupOutcome:
    """What one :func:`run_residue_cleanup` invocation did.

    Exactly one of the terminal ``clean`` / ``unreadable`` / ``head_moved``
    / ``unmerged`` / ``operation_blocked`` / ``operation_unknown`` /
    ``untracked_only`` / ``refused`` flags is normally set when no cleanup
    ran; a blocking cleanup sets none of them and reports its effect
    through ``action`` / ``restored_paths`` / ``remaining_paths``.
    ``degraded`` names a rung the executor could only execute at quarantine
    strength (fresh-worktree / environment rungs need machinery owned
    elsewhere); the tree is still preserved and restored.

    ``operation_blocked`` names the seam where a git operation marker
    (``MERGE_HEAD`` / ``rebase-merge`` / ``rebase-apply`` /
    ``CHERRY_PICK_HEAD``) proved an operation in progress: the operation
    owns the index and any staged resolution, so cleanup takes no
    restore/reset action and ``operation_kind`` names what was observed.
    ``operation_unknown`` is the fail-closed variant: the marker probe was
    unreadable, which is never evidence of "no operation".
    """

    clean: bool = False
    unreadable: bool = False
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
    """Map of tracked (non-untracked, non-``.agent/``) path -> XY code."""
    entries: dict[str, str] = {}
    for entry in parse_porcelain_z(status_blob):
        if entry.xy == "??" or entry.path.startswith(_PERMITTED_ARTIFACT_PREFIX):
            continue
        entries[entry.path] = entry.xy
    return entries


def _untracked_paths(status_blob: str) -> tuple[str, ...]:
    """Sorted untracked paths outside ``.agent/``."""
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
) -> str:
    """Content fingerprint of the tracked view: HEAD + entries + diff hash."""
    digest = hashlib.sha256()
    digest.update(head_sha.encode("utf-8"))
    digest.update(b"\0")
    for path in sorted(tracked_entries):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(tracked_entries[path].encode("utf-8"))
        digest.update(b"\0")
    digest.update(tracked_diff_hash.encode("utf-8"))
    return digest.hexdigest()


def detect_planning_residue(
    baseline: PlanningBaseline,
    after_blob: str,
    *,
    after_diff_hash: str,
    incoming_overwrite_paths: frozenset[str] = frozenset(),
) -> PlanningResidue | None:
    """Pure diff of the post-dispatch tree against the baseline.

    Returns ``None`` when planning left nothing detectable. The comparison
    never reads the filesystem: everything is derived from the two porcelain
    blobs and the two diff hashes.
    """
    if after_blob == baseline.status_blob and after_diff_hash == baseline.tracked_diff_hash:
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
    # A path dirty at baseline whose porcelain row vanished (deleted or
    # committed) during the dispatch: the human work is gone from the tree;
    # report it as ambiguous, never silently "restored".
    ambiguous.extend(
        path
        for path in sorted(before_tracked)
        if path not in after_tracked and path not in after_untracked
    )

    # Content-only change on a path whose porcelain row did not move:
    # the diff hash moved but no per-path row did. Conservatively treat
    # every currently-tracked-dirty path as ambiguous (save, never revert).
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
    if not (tracked or ambiguous or new_untracked or overwritten or unmerged):
        return None
    fingerprint = compute_fingerprint(
        head_sha=baseline.head_sha,
        tracked_entries=after_tracked,
        tracked_diff_hash=after_diff_hash,
    )
    return PlanningResidue(
        fingerprint=fingerprint,
        tracked_paths=tuple(tracked),
        ambiguous_paths=tuple(ambiguous),
        untracked_paths=new_untracked,
        overwritten_untracked_paths=overwritten,
        unmerged_paths=unmerged,
    )


def residue_observation(residue: PlanningResidue) -> tuple[Observation, DirtShape]:
    """Project a residue onto the decision table's ``(observation, shape)``.

    The dirty-tree branch of the table only reads worktree class,
    in-progress kind, record and head state, plus the shape/attribution
    refinements; the target axes are fixed to the in-sync pair because the
    residue seam runs after the planning-sync gate has already proven them.
    """
    if residue.unmerged_paths:
        worktree = WorktreeState.UNMERGED
        shape = DirtShape.UNMERGED
    elif residue.tracked_paths or residue.ambiguous_paths:
        worktree = WorktreeState.TRACKED_CHANGES
        shape = DirtShape.UNSTAGED_MODIFICATION
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
        # The director's projection pairs kind NONE with ownership OWNED;
        # UNKNOWN here would read as an unreadable probe and the table
        # would answer WAIT_FOR_ENVIRONMENT instead of a cleanup action.
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.NONE,
        agent_active=False,
    ), shape


def residue_attribution(residue: PlanningResidue) -> DirtAttribution:
    """Attribution the decision seam charges with.

    Ambiguous paths (human work mixed with planning edits) force UNKNOWN —
    the table's conservative save-never-revert attribution. Unambiguous
    residue is UNOWNED. Planning residue is never PHASE_OWNED: planning has
    no commit seam, so the table can never route it to
    ``COMMIT_VIA_COMMIT_PHASE`` (which would commit residue as feature work).
    """
    if residue.ambiguous_paths:
        return DirtAttribution.UNKNOWN
    return DirtAttribution.UNOWNED


# ---------------------------------------------------------------------------
# Ports: the impure edges behind narrow seams.
# ---------------------------------------------------------------------------


class CleanupGitPort(Protocol):
    """Git + state-file seam the residue executor performs its effects through.

    Every method is effect-free or a deliberate, named mutation; tests
    substitute an in-memory fake so the executor is black-box testable with
    no subprocess, no filesystem and no clock.
    """

    def porcelain_status_z(self, root: Path) -> str | None:
        """Raw ``git status --porcelain -z`` (untracked normal); None when unreadable."""
        ...

    def tracked_diff_hash(self, root: Path) -> str | None:
        """SHA-256 of ``git diff --no-renames HEAD``; None when unreadable."""
        ...

    def head_sha(self, root: Path) -> str | None:
        """Current HEAD sha; None when unborn/unreadable."""
        ...

    def in_progress_kind(self, root: Path) -> Reading[InProgressKind]:
        """Operation-marker probe (``MERGE_HEAD`` / rebase dirs / ``CHERRY_PICK_HEAD``).

        :meth:`Reading.unknown` when the probe is unreadable — never
        positive evidence that no operation exists.
        """
        ...

    def snapshot_worktree(self, root: Path, *, head_sha: str) -> str:
        """Create the durable backup ref for the current tree; return its name."""
        ...

    def ref_exists(self, root: Path, ref: str) -> bool:
        """Whether ``ref`` resolves."""
        ...

    def restore_paths(self, root: Path, *, source: str, paths: Sequence[str]) -> bool:
        """Restore worktree+index ``paths`` from ``source`` (baseline commit)."""
        ...

    def preserve_dirt_commit(
        self, root: Path, *, head_sha: str, paths: Sequence[str]
    ) -> str | None:
        """Alternate-preservation rung: commit the dirt via plumbing.

        Returns the preservation ref, or ``None`` when there was nothing to
        preserve. Never touches the caller's index or worktree.
        """
        ...

    def quarantine_paths(
        self,
        root: Path,
        paths: Sequence[str],
        *,
        quarantine_dir: Path,
    ) -> tuple[str, ...]:
        """Move ``paths`` aside into ``quarantine_dir``; return the moved paths."""
        ...

    def incoming_overwrite_paths(self, root: Path, target: str | None) -> frozenset[str]:
        """Paths the incoming target commits would add or change vs HEAD."""
        ...

    def load_state(self, root: Path) -> Mapping[str, object] | None:
        """The persisted cleanup-progress payload, if any."""
        ...

    def store_state(self, root: Path, payload: Mapping[str, object]) -> None:
        """Persist cleanup progress for resume-after-interruption."""
        ...

    def clear_state(self, root: Path) -> None:
        """Drop cleanup progress once nothing remains to resume."""
        ...

    def path_exists(self, path: Path) -> bool:
        """Whether ``path`` exists (destination probe for the landing restore)."""
        ...

    def move_path(self, source: str, destination: Path) -> None:
        """Move ``source`` back to ``destination`` (may raise ``OSError``)."""
        ...


class ResidueLedgerPort(Protocol):
    """Charged-before-action seam onto the repository-wide attempt ledger.

    The implementation loads the durable ledger, asks the decision table
    for the cleanup action (direct or escalated under repeat pressure) and
    charges the attempt — all BEFORE the caller mutates anything. Intent
    before, outcome after.
    """

    def charge_residue_attempt(self, root: Path, *, residue: PlanningResidue) -> NextAction:
        """Charge one residue attempt and return the decided cleanup action."""
        ...


# ---------------------------------------------------------------------------
# Cleanup progress record (durable, resumable).
# ---------------------------------------------------------------------------


@dataclass
class _CleanupProgress:
    """Mutable resume record for one residue cleanup.

    A local object only: never module-level, never ``self.X``, so the
    resource-accumulator audit does not apply. Persisted via the port as a
    JSON mapping after every step, so a kill between the backup ref and the
    restore resumes without a second backup of the same content.
    """

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


# ---------------------------------------------------------------------------
# The executor.
# ---------------------------------------------------------------------------


def capture_planning_baseline(
    root: Path,
    *,
    git: CleanupGitPort,
) -> PlanningBaseline | None:
    """Record the pre-planning-dispatch baseline; ``None`` when unreadable.

    When the baseline tree already carries tracked dirt (pre-existing human
    work present despite the readiness gate), the baseline also snapshots
    the tree under a durable backup ref so a later ambiguity verdict can
    preserve that human work instead of losing it.
    """
    blob = git.porcelain_status_z(root)
    diff_hash = git.tracked_diff_hash(root)
    head = git.head_sha(root)
    if blob is None or diff_hash is None or head is None:
        return None
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
    """Detect and clear planning residue left by one dispatch.

    Never raises: every git effect goes through the port, an unreadable
    observation is a typed outcome (never "clean"), and an unexpected port
    failure is logged and reported as ``refused`` so the dispatch that
    already ran is never crashed by its own cleanup.
    """
    emit = warn if warn is not None else _default_warn
    try:
        return _run_residue_cleanup(
            root, baseline=baseline, git=git, ledger=ledger, target=target, emit=emit
        )
    except Exception as exc:  # pragma: no cover -- defensive: cleanup never crashes planning
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
    # Operation gate before anything else (see _operation_gate).
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
    head: str,
    emit: Callable[[str], None],
) -> ResidueCleanupOutcome:
    """Diff the fresh observation against the baseline and act on the result.

    Runs only after the unreadable-observation guard and the operation
    gate have both passed. Compare-and-act: a moved HEAD means the
    baseline no longer describes the tree; restoring against it could
    rewrite somebody else's commit.
    """
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
        residue.tracked_paths or residue.ambiguous_paths or residue.overwritten_untracked_paths
    ):
        # Untracked-only residue does not block: planning continues with the
        # files in place. No ledger charge — nothing mutated.
        return ResidueCleanupOutcome(untracked_only=True)
    return _cleanup_blocking_residue(root, baseline, git, ledger, residue, emit)


def _operation_gate(
    root: Path,
    git: CleanupGitPort,
    emit: Callable[[str], None],
) -> ResidueCleanupOutcome | None:
    """Defer cleanup while ANY git operation marker is present.

    A git operation marker (``MERGE_HEAD``, ``rebase-merge/``,
    ``rebase-apply/``, ``CHERRY_PICK_HEAD``) proves an operation owns the
    index — its staged resolution is NEVER planning residue. An
    all-resolved merge (conflicts resolved and staged, ``MERGE_HEAD`` still
    present) would otherwise reach ``restore_paths`` against the baseline
    and destroy the operation's staged resolution. No owned/foreign split
    at this seam: ANY marker defers cleanup, and the operation ladder
    (continue/abort/rebuild) owns this state, not the residue cleanup.

    An unreadable probe is UNKNOWN, never "clean/none": fail closed with a
    typed outcome and no restore. Returns ``None`` when no marker gates
    cleanup and the caller proceeds.
    """
    in_progress = git.in_progress_kind(root)
    if not in_progress.is_known_good:
        emit(
            "operation-state probe unreadable; cleanup refusing to act "
            "on an unknown operation state (tree left untouched)"
        )
        return ResidueCleanupOutcome(operation_unknown=True)
    kind = cast("InProgressKind", in_progress.value)
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
    """Charge, save, restore and report one blocking residue event."""
    action = ledger.charge_residue_attempt(root, residue=residue)
    if action not in _CLEANUP_LADDER_ACTIONS:
        emit(
            f"decision table returned non-cleanup action {action.value} for "
            "planning residue; refusing to run it (residue is never committed "
            "as feature work)"
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
    """Save BEFORE any restore.

    The backup-ref name derives from the tree content, so an existing
    recorded ref proves the identical content is already preserved: a
    resumed run skips the snapshot — never a second backup of the same
    content.
    """
    if progress.backup_ref is None or not git.ref_exists(root, progress.backup_ref):
        progress.backup_ref = git.snapshot_worktree(root, head_sha=baseline.head_sha)
        progress.restored_paths = []


def _quarantine_overwrite_blockers(
    root: Path,
    git: CleanupGitPort,
    progress: _CleanupProgress,
    residue: PlanningResidue,
) -> tuple[str, ...]:
    """Move untracked incoming-overwrite blockers aside; return the moved paths."""
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
    """Run the rung-specific preservation, restore the remainder, return targets."""
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
    """Re-observe after the restore and persist resumable progress.

    Only paths still dirty count as remaining. An unreadable re-observation
    fails closed: nothing is marked restored. Returns ``(remaining,
    restored_this_run)``.
    """
    final_blob = git.porcelain_status_z(root)
    final_diff = git.tracked_diff_hash(root)
    if final_blob is None or final_diff is None:
        still_dirty = set(restore_targets)
    else:
        final_residue = detect_planning_residue(
            baseline,
            final_blob,
            after_diff_hash=final_diff,
        )
        still_dirty = set(final_residue.tracked_paths) if final_residue is not None else set()
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
    """The ONE warning that names what was restored, saved and preserved."""
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
    """Run the rung-specific preservation step that precedes the restore."""
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
    # QUARANTINE_AND_RESTORE and every degraded stronger rung: move the
    # residue files aside (a second preservation, on disk) before the
    # restore recreates them from the baseline commit.
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
    """Load matching progress for a resume, or start a fresh record.

    A recorded fingerprint identical to the current residue proves the
    identical content is already backed up: the caller then skips the
    snapshot. Quarantined untracked files from an older, different residue
    are carried over so their restore-after-landing record is never lost.
    """
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
        restore_paths=list(residue.tracked_paths),
        ambiguous_paths=list(residue.ambiguous_paths),
        quarantined_untracked=carried_quarantine,
    )


def _retire_state_when_done(root: Path, git: CleanupGitPort) -> None:
    """Drop a stale progress record once the tree is clean again.

    A record with quarantined untracked files survives: the landing seam
    still owes those paths their restore-after-landing.
    """
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
    """Move quarantined untracked files back; return the restored paths.

    The landing seam calls this after the feature lands so an untracked
    file the incoming target commits would have overwritten comes back
    instead of being silently clobbered. Paths whose destination now exists
    are left quarantined (the landing content wins and the preserved copy
    stays reachable in the quarantine dir); the returned tuple names only
    what was actually restored.
    """
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


# ---------------------------------------------------------------------------
# Real ports (production edges).
# ---------------------------------------------------------------------------


class RunGitCleanupPort:
    """Production :class:`CleanupGitPort`: every effect through ``run_git``.

    ``run_git`` serializes each call through the per-common-dir command
    queue (the mutation lease) and applies the non-interactive hardening.
    All invocations here are plumbing-class — porcelain reads, ``stash
    create`` / ``commit-tree`` / ``update-ref`` preservation, ``restore``
    for the tree — which run no hooks by construction. State-file I/O goes
    through the shared :class:`~ralph.mcp.artifacts.file_backend.FileBackend`
    primitive (the filesystem-write consolidation seam), never raw paths.
    """

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
        """Preserve the dirt as a normal commit over a plumbing-built tree.

        ``git stash create`` materializes the current index+worktree state
        as a commit WITHOUT touching ``refs/stash``, the index or the tree;
        re-committing its tree over ``head_sha`` with ``commit-tree`` keeps
        the staged and worktree variants reachable from one ordinary commit
        ref. No hook can fire: every step is plumbing.
        """
        del paths  # stash create captures the whole state; the residue paths are included.
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
                # Missing source (deleted residue) or a directory the
                # replace seam will not move: leave it in place and let the
                # re-observation report what is still dirty.
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
    """Production :class:`ResidueLedgerPort` onto the owner seam.

    Loads the durable attempt ledger through the owner's single read seam,
    asks the decision table for the cleanup action (direct or escalated),
    and charges the attempt through the owner's single write seam — all
    before the caller touches the tree. The owner import is lazy because
    the owner delegates its ledger work back through this package.
    """

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
