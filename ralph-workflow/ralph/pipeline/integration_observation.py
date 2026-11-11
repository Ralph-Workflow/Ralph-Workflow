
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Final

from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.git.subprocess_runner import run_git
from ralph.pipeline._index_flag_records import IndexFlagDirt, read_index_flag_dirt
from ralph.pipeline._porcelain_records import (
    _decode_quoted_porcelain_path,
    _porcelain_to_shape,
    _porcelain_to_state,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class ReadingVariant(Enum):

    KNOWN_GOOD = "known_good"
    KNOWN_BLOCKED = "known_blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Reading[T]:

    variant: ReadingVariant
    value: T | None = None
    reason: str | None = None

    @classmethod
    def known(cls, value: T) -> Reading[T]:
        return cls(ReadingVariant.KNOWN_GOOD, value=value)

    @classmethod
    def known_blocked(cls, reason: str) -> Reading[T]:
        return cls(ReadingVariant.KNOWN_BLOCKED, reason=reason)

    @classmethod
    def unknown(cls, reason: str) -> Reading[T]:
        return cls(ReadingVariant.UNKNOWN, reason=reason)

    @property
    def is_known_good(self) -> bool:
        return self.variant is ReadingVariant.KNOWN_GOOD

    def require_known(self) -> T:
        if self.variant is not ReadingVariant.KNOWN_GOOD or self.value is None:
            raise ValueError(
                f"probe result is not known-good (variant={self.variant!r}): "
                f"{self.reason or 'no reason recorded'}"
            )
        return self.value

    @property
    def is_known_blocked(self) -> bool:
        return self.variant is ReadingVariant.KNOWN_BLOCKED

    @property
    def is_unknown(self) -> bool:
        return self.variant is ReadingVariant.UNKNOWN


def reading_from_callable[
    T,
](
    probe: Callable[[], T],
    *,
    classify_blocked: Callable[[Exception], str | None] = lambda _exc: None,
) -> Reading[T]:
    try:
        return Reading.known(probe())
    except Exception as exc:
        reason = classify_blocked(exc)
        if isinstance(reason, str) and reason:
            return Reading[T].known_blocked(reason)
        return Reading[T].unknown(str(exc) or type(exc).__name__)


class TargetVsFeature(Enum):

    EQUAL = "equal"
    BEHIND = "behind"
    AHEAD = "ahead"
    DIVERGED = "diverged"
    UNKNOWN = "unknown"


class TargetVsRemote(Enum):

    EQUAL = "equal"
    BEHIND = "behind"
    AHEAD = "ahead"
    DIVERGED = "diverged"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"


class WorktreeState(Enum):

    CLEAN = "clean"
    TRACKED_CHANGES = "tracked_changes"
    STAGED = "staged"
    UNTRACKED = "untracked"
    UNMERGED = "unmerged"
    UNKNOWN = "unknown"


class DirtShape(Enum):

    NONE = "none"
    UNSTAGED_MODIFICATION = "unstaged_modification"
    STAGED_ONLY = "staged_only"
    STAGED_AND_UNSTAGED = "staged_and_unstaged"
    MODE_ONLY = "mode_only"
    INTENT_TO_ADD = "intent_to_add"
    TYPE_CHANGE = "type_change"
    DELETED = "deleted"
    RENAMED_OR_COPIED = "renamed_or_copied"
    UNTRACKED_ONLY = "untracked_only"
    UNTRACKED_BLOCKING = "untracked_blocking"
    LINE_ENDING_ONLY = "line_ending_only"
    INDEX_FLAG_HIDDEN = "index_flag_hidden"
    UNMERGED = "unmerged"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DirtEvidence:

    mode_only_paths: frozenset[str] = frozenset()
    empty_diff_paths: frozenset[str] = frozenset()
    incoming_overwrite_paths: frozenset[str] = frozenset()


class HeadState(Enum):

    ATTACHED = "attached"
    DETACHED = "detached"
    UNKNOWN = "unknown"


class InProgressKind(Enum):

    NONE = "none"
    REBASE = "rebase"
    MERGE = "merge"
    CHERRY_PICK = "cherry_pick"
    REVERT = "revert"
    AM = "am"
    SEQUENCER = "sequencer"


class InProgressOwnership(Enum):

    OWNED = "owned"
    FOREIGN = "foreign"
    UNKNOWN = "unknown"


class IntegrationRecordState(Enum):

    NONE = "none"
    CONSISTENT = "consistent"
    STALE = "stale"
    UNREADABLE = "unreadable"
    OLDER_VERSION = "older_version"
    FOREIGN = "foreign"


@dataclass(frozen=True, slots=True)
class Observation:

    target_vs_feature: TargetVsFeature
    target_vs_remote: TargetVsRemote
    worktree: WorktreeState
    head: HeadState
    in_progress_kind: InProgressKind
    in_progress_ownership: InProgressOwnership
    record: IntegrationRecordState
    agent_active: bool
    _has_unknown: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_has_unknown",
            (
                self.target_vs_feature is TargetVsFeature.UNKNOWN
                or self.target_vs_remote is TargetVsRemote.UNKNOWN
                or self.worktree is WorktreeState.UNKNOWN
                or self.head is HeadState.UNKNOWN
                or self.in_progress_ownership is InProgressOwnership.UNKNOWN
            ),
        )

    def has_unknown(self) -> bool:
        return self._has_unknown




class WorktreeDispatch(Enum):

    ALLOWED = "allowed"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class WorktreeVerdict:

    state: WorktreeState
    is_clean: bool
    dispatch: WorktreeDispatch
    reason: str
    dirt_shape: DirtShape = DirtShape.NONE

    @property
    def dispatch_allowed(self) -> bool:
        return self.dispatch is WorktreeDispatch.ALLOWED

    @property
    def dispatch_blocked(self) -> bool:
        return self.dispatch is WorktreeDispatch.BLOCKED

    @property
    def needs_reobservation(self) -> bool:
        return self.dispatch is WorktreeDispatch.UNKNOWN


_CLEAN: Final[WorktreeVerdict] = WorktreeVerdict(
    state=WorktreeState.CLEAN,
    is_clean=True,
    dispatch=WorktreeDispatch.ALLOWED,
    reason="worktree clean",
    dirt_shape=DirtShape.NONE,
)
_TRACKED_CHANGES: Final[WorktreeVerdict] = WorktreeVerdict(
    state=WorktreeState.TRACKED_CHANGES,
    is_clean=False,
    dispatch=WorktreeDispatch.BLOCKED,
    reason="uncommitted tracked changes in the worktree",
    dirt_shape=DirtShape.UNSTAGED_MODIFICATION,
)
_STAGED: Final[WorktreeVerdict] = WorktreeVerdict(
    state=WorktreeState.STAGED,
    is_clean=False,
    dispatch=WorktreeDispatch.BLOCKED,
    reason="staged changes waiting to be committed",
    dirt_shape=DirtShape.STAGED_ONLY,
)
_UNMERGED: Final[WorktreeVerdict] = WorktreeVerdict(
    state=WorktreeState.UNMERGED,
    is_clean=False,
    dispatch=WorktreeDispatch.BLOCKED,
    reason="unmerged paths remain from an unfinished rebase or merge",
    dirt_shape=DirtShape.UNMERGED,
)
_UNTRACKED: Final[WorktreeVerdict] = WorktreeVerdict(
    state=WorktreeState.UNTRACKED,
    is_clean=True,
    dispatch=WorktreeDispatch.ALLOWED,
    reason="untracked scratch paths only; they never block a gate",
    dirt_shape=DirtShape.UNTRACKED_ONLY,
)


def _verdict_for_state(state: WorktreeState) -> WorktreeVerdict:
    if state is WorktreeState.CLEAN:
        return _CLEAN
    if state is WorktreeState.TRACKED_CHANGES:
        return _TRACKED_CHANGES
    if state is WorktreeState.STAGED:
        return _STAGED
    if state is WorktreeState.UNMERGED:
        return _UNMERGED
    if state is WorktreeState.UNTRACKED:
        return _UNTRACKED
    return WorktreeVerdict(
        state=WorktreeState.UNKNOWN,
        is_clean=False,
        dispatch=WorktreeDispatch.UNKNOWN,
        reason="worktree probe did not answer",
        dirt_shape=DirtShape.UNKNOWN,
    )


def _verdict_for_porcelain(
    text: str,
    evidence: DirtEvidence | None,
    index_flag_dirt: IndexFlagDirt | None,
) -> WorktreeVerdict:
    state = _porcelain_to_state(text)
    shape = _porcelain_to_shape(text, evidence)
    verdict = _verdict_for_state(state)
    if (
        index_flag_dirt is not None
        and index_flag_dirt.changed_paths
        and shape is not DirtShape.UNMERGED
    ):
        hidden = ", ".join(sorted(index_flag_dirt.changed_paths))
        return WorktreeVerdict(
            state=WorktreeState.TRACKED_CHANGES,
            is_clean=False,
            dispatch=WorktreeDispatch.BLOCKED,
            reason=(
                "index-flag-hidden modified tracked paths "
                f"(assume-unchanged/skip-worktree): {hidden}"
            ),
            dirt_shape=DirtShape.INDEX_FLAG_HIDDEN,
        )
    if verdict.dirt_shape is shape:
        return verdict
    if shape is DirtShape.LINE_ENDING_ONLY:
        return WorktreeVerdict(
            state=state,
            is_clean=True,
            dispatch=WorktreeDispatch.ALLOWED,
            reason="line-ending-only changes; clean after index refresh",
            dirt_shape=shape,
        )
    if shape is DirtShape.UNTRACKED_BLOCKING:
        return WorktreeVerdict(
            state=state,
            is_clean=False,
            dispatch=WorktreeDispatch.BLOCKED,
            reason="untracked paths would be overwritten by incoming target commits",
            dirt_shape=shape,
        )
    return WorktreeVerdict(
        state=state,
        is_clean=verdict.is_clean,
        dispatch=verdict.dispatch,
        reason=verdict.reason,
        dirt_shape=shape,
    )


def classify_worktree(
    observation: Reading[str] | WorktreeState,
    *,
    evidence: DirtEvidence | None = None,
    index_flag_dirt: IndexFlagDirt | None = None,
) -> WorktreeVerdict:
    if isinstance(observation, WorktreeState):
        return _verdict_for_state(observation)
    if not observation.is_known_good:
        return WorktreeVerdict(
            state=WorktreeState.UNKNOWN,
            is_clean=False,
            dispatch=WorktreeDispatch.UNKNOWN,
            reason=observation.reason or "worktree probe did not answer",
            dirt_shape=DirtShape.UNKNOWN,
        )
    return _verdict_for_porcelain(observation.value or "", evidence, index_flag_dirt)




def _unreadable(what: str, detail: str) -> Reading[str]:
    return Reading[str].unknown(f"{what} unreadable: {detail}")


def read_worktree_porcelain(root: Path) -> Reading[str]:
    try:
        result = run_git(
            (*COMMIT_PIN_CONFIG_ARGS, "status", "--porcelain", "-z", "--untracked-files=normal"),
            cwd=root,
            label="integration-observation-porcelain",
        )
    except Exception as exc:
        return _unreadable("git status --porcelain", str(exc) or type(exc).__name__)
    if result.returncode != 0:
        return _unreadable("git status --porcelain", f"exit={result.returncode}")
    return Reading.known(result.stdout)


def read_head_state(root: Path) -> Reading[HeadState]:
    try:
        result = run_git(
            (*COMMIT_PIN_CONFIG_ARGS, "symbolic-ref", "--quiet", "--", "HEAD"),
            cwd=root,
            label="integration-observation-head",
        )
    except Exception as exc:
        return Reading[HeadState].unknown(
            f"git symbolic-ref HEAD unreadable: {exc or type(exc).__name__}"
        )
    if result.returncode == 0:
        return Reading.known(HeadState.ATTACHED)
    if result.returncode == 1:
        return Reading.known(HeadState.DETACHED)
    return Reading[HeadState].unknown(f"git symbolic-ref HEAD unreadable: exit={result.returncode}")


def read_merge_base(root: Path, left: str, right: str) -> tuple[str | None, bool]:
    try:
        result = run_git(
            (*COMMIT_PIN_CONFIG_ARGS, "merge-base", left, right),
            cwd=root,
            label="integration-observation-merge-base",
        )
    except Exception:
        return None, False
    if result.returncode == 0:
        return result.stdout.strip() or None, True
    if result.returncode == 1:
        return None, True
    return None, False


def _marker_present(path: Path) -> bool:
    try:
        path.stat()
    except FileNotFoundError:
        return False
    return True


def _am_patch_counters_present(rebase_apply: Path) -> bool:
    return _marker_present(rebase_apply / "next") and _marker_present(rebase_apply / "last")


_SINGLE_OPERATION_MARKERS: tuple[tuple[str, InProgressKind], ...] = (
    ("MERGE_HEAD", InProgressKind.MERGE),
    ("CHERRY_PICK_HEAD", InProgressKind.CHERRY_PICK),
    ("REVERT_HEAD", InProgressKind.REVERT),
    ("sequencer", InProgressKind.SEQUENCER),
)


def _read_operation_markers(git_dir: Path) -> Reading[InProgressKind]:
    try:
        if _marker_present(git_dir / "rebase-merge"):
            return Reading.known(InProgressKind.REBASE)
        rebase_apply = git_dir / "rebase-apply"
        if _marker_present(rebase_apply):
            kind = (
                InProgressKind.AM
                if _am_patch_counters_present(rebase_apply)
                else InProgressKind.REBASE
            )
            return Reading.known(kind)
        for marker_name, kind in _SINGLE_OPERATION_MARKERS:
            if _marker_present(git_dir / marker_name):
                return Reading.known(kind)
    except OSError as exc:
        return Reading[InProgressKind].unknown(
            f"in-progress marker unreadable: {exc or type(exc).__name__}"
        )
    return Reading.known(InProgressKind.NONE)


def read_in_progress_kind(root: Path) -> Reading[InProgressKind]:
    try:
        from ralph.git.operations import _resolve_gitdir_paths

        gitdir, _commondir = _resolve_gitdir_paths(root)
        if gitdir is not None and gitdir.is_dir():
            return _read_operation_markers(gitdir)
    except (OSError, ValueError):
        gitdir = None
    try:
        result = run_git(
            (*COMMIT_PIN_CONFIG_ARGS, "rev-parse", "--absolute-git-dir"),
            cwd=root,
            label="integration-observation-git-dir",
        )
    except Exception as exc:
        return Reading[InProgressKind].unknown(
            f"git directory unreadable: {exc or type(exc).__name__}"
        )
    if result.returncode != 0:
        return Reading[InProgressKind].unknown(
            f"git directory unreadable: exit={result.returncode}"
        )
    git_dir_raw = result.stdout.strip()
    if not git_dir_raw:
        return Reading[InProgressKind].unknown("git directory unreadable: empty path")
    return _read_operation_markers(Path(git_dir_raw))


def read_worktree(root: Path) -> Reading[WorktreeVerdict]:
    porcelain = read_worktree_porcelain(root)
    if not porcelain.is_known_good:
        return Reading[WorktreeVerdict].unknown(porcelain.reason or "worktree probe did not answer")
    flags = read_index_flag_dirt(root, git_runner=run_git)
    if not flags.is_known_good:
        return Reading[WorktreeVerdict].unknown(flags.reason or "index-flag probe did not answer")
    return Reading.known(classify_worktree(porcelain, index_flag_dirt=flags.value))


__all__ = [
    "DirtEvidence",
    "DirtShape",
    "HeadState",
    "InProgressKind",
    "InProgressOwnership",
    "IndexFlagDirt",
    "IntegrationRecordState",
    "Observation",
    "Reading",
    "ReadingVariant",
    "TargetVsFeature",
    "TargetVsRemote",
    "WorktreeDispatch",
    "WorktreeState",
    "WorktreeVerdict",
    "_decode_quoted_porcelain_path",
    "classify_worktree",
    "read_head_state",
    "read_in_progress_kind",
    "read_index_flag_dirt",
    "read_merge_base",
    "read_worktree",
    "read_worktree_porcelain",
    "reading_from_callable",
]
