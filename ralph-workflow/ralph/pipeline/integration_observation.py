"""Typed three-valued observation model for Ralph's auto-integration subsystem.

Every probe that the integration boundary inspects at a rebase/merge seam
must collapse to one of three readings:

* :class:`Reading.KnownGood` - the probe ran and returned a real value.
* :class:`Reading.KnownBlocked` - the probe ran and proved a blocking fact
  (for example, a foreign agent owns the in-progress rebase).
* :class:`Reading.Unknown` - the probe raised, the tool was unavailable,
  or the result could not be classified. Exceptions are NEVER promoted to
  :class:`Reading.KnownBlocked`; a thrown error is always informational
  unknown so the decision table can yield :class:`NextAction.WAIT_FOR_ENVIRONMENT`
  instead of dispatching an agent that would race with the live failure.

The seven :class:`Observation` dimensions the integration decision table
consumes share an exhaustive enum vocabulary so the table can be walked as
a finite-state function. Every dimension that the spec marks unknown-capable
carries a dedicated ``UNKNOWN`` member; probe helpers exist that lift
exceptions into :class:`Reading.Unknown` then derive the appropriate
enum member from a successful value.

This module also owns **the single worktree classifier**,
:func:`classify_worktree`, which turns one worktree observation into a
:class:`WorktreeState` plus a :class:`WorktreeDispatch` verdict and a
:class:`DirtShape` refinement. The clean/blocked question was previously
answered by four or five independent probes with four or five
definitions; that disagreement is what let one seam integrate while
another refused. Every gate must call :func:`classify_worktree` instead
of re-deriving cleanliness. The dirt shape is the typed answer to "what
KIND of uncommitted content is this" — the decision table maps each
shape to exactly one deterministic cleanup action.
"""

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
    """Closed tag for :class:`Reading` variants."""

    KNOWN_GOOD = "known_good"
    KNOWN_BLOCKED = "known_blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Reading[T]:
    """Three-valued result of a single integration-state probe.

    ``value`` is populated only when ``variant`` is :attr:`ReadingVariant.KNOWN_GOOD`;
    ``reason`` carries a human-readable explanation for the other two variants.
    """

    variant: ReadingVariant
    value: T | None = None
    reason: str | None = None

    @classmethod
    def known(cls, value: T) -> Reading[T]:
        """Wrap a confirmed probe result."""
        return cls(ReadingVariant.KNOWN_GOOD, value=value)

    @classmethod
    def known_blocked(cls, reason: str) -> Reading[T]:
        """Wrap a confirmed blocking probe result."""
        return cls(ReadingVariant.KNOWN_BLOCKED, reason=reason)

    @classmethod
    def unknown(cls, reason: str) -> Reading[T]:
        """Wrap an unclassifiable probe result (exceptions live here)."""
        return cls(ReadingVariant.UNKNOWN, reason=reason)

    @property
    def is_known_good(self) -> bool:
        """True only when the probe succeeded."""
        return self.variant is ReadingVariant.KNOWN_GOOD

    def require_known(self) -> T:
        """Return the confirmed value, raising when the probe was not KNOWN_GOOD.

        Runtime-checked alternative to casting ``value`` after an
        ``is_known_good`` guard (the cast policy forbids asserting the
        shape of probe results without a check).
        """
        if self.variant is not ReadingVariant.KNOWN_GOOD or self.value is None:
            raise ValueError(
                f"probe result is not known-good (variant={self.variant!r}): "
                f"{self.reason or 'no reason recorded'}"
            )
        return self.value

    @property
    def is_known_blocked(self) -> bool:
        """True only when the probe proved a blocking fact."""
        return self.variant is ReadingVariant.KNOWN_BLOCKED

    @property
    def is_unknown(self) -> bool:
        """True only when the probe could not classify the answer."""
        return self.variant is ReadingVariant.UNKNOWN


def reading_from_callable[
    T,
](
    probe: Callable[[], T],
    *,
    classify_blocked: Callable[[Exception], str | None] = lambda _exc: None,
) -> Reading[T]:
    """Lift a probe into :class:`Reading`, treating exceptions as unknown.

    The default ``classify_blocked`` returns ``None`` for every exception,
    so any raised error becomes :meth:`Reading.unknown`. A subclass may
    opt in to :meth:`Reading.known_blocked` for a narrow exception class
    by returning a string reason from ``classify_blocked``.
    """
    try:
        return Reading.known(probe())
    except Exception as exc:
        reason = classify_blocked(exc)
        if isinstance(reason, str) and reason:
            return Reading[T].known_blocked(reason)
        return Reading[T].unknown(str(exc) or type(exc).__name__)


class TargetVsFeature(Enum):
    """Relationship between the feature branch tip and the target branch tip."""

    EQUAL = "equal"
    BEHIND = "behind"
    AHEAD = "ahead"
    DIVERGED = "diverged"
    UNKNOWN = "unknown"


class TargetVsRemote(Enum):
    """Relationship between the target branch tip and its remote counterpart."""

    EQUAL = "equal"
    BEHIND = "behind"
    AHEAD = "ahead"
    DIVERGED = "diverged"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"


class WorktreeState(Enum):
    """State of the local working tree relative to tracked and untracked changes."""

    CLEAN = "clean"
    TRACKED_CHANGES = "tracked_changes"
    STAGED = "staged"
    UNTRACKED = "untracked"
    UNMERGED = "unmerged"
    UNKNOWN = "unknown"


class DirtShape(Enum):
    """Typed refinement of WHAT KIND of dirt the worktree carries.

    :class:`WorktreeState` answers "does the tree block a gate"; the dirt
    shape answers "what kind of uncommitted content is this", which the
    decision table needs to pick the deterministic cleanup action
    (commit-phase commit for phase-owned dirt, save-and-restore for
    residue, nothing at all for dirt that a refresh or a per-file git
    refusal already handles).

    The vocabulary is closed and total: every observable tree maps to
    exactly one shape. ``NONE`` is a clean tree, ``UNMERGED`` is a real
    conflict (the operation ladder owns it, never the cleanup path), and
    ``UNKNOWN`` is an unreadable probe. ``INDEX_FLAG_HIDDEN`` is tracked
    content a ``assume-unchanged`` / ``skip-worktree`` index flag hides
    from porcelain, from ``git diff HEAD`` and from ``git stash create``
    (all verified against git itself): the porcelain-only probes read
    the tree as clean, so the dedicated index-flag probe
    (:func:`read_index_flag_dirt`) is the only evidence that proves the
    hidden edit. The shape blocks exactly like ordinary tracked residue
    and routes to the same preserve-then-clean ladder.
    """

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
    """Optional probe evidence that refines the dirt shape beyond porcelain.

    Porcelain alone cannot distinguish several dirt shapes. A caller that
    has run the cheap content probes hands their answers in; every field
    defaults to "no evidence", in which case the shape falls back to what
    porcelain alone can prove. All collections are frozensets: the
    evidence is immutable, hashable and cannot grow at runtime.

    * ``mode_only_paths`` — paths whose only change is a file-mode change
      (``git diff --summary`` evidence). Mode-only dirt is real tracked
      dirt, but it is typed so the cleanup can restore modes without
      touching content.
    * ``empty_diff_paths`` — paths porcelain reports dirty whose content
      diff is empty (autocrlf renormalisation, racy-git index entries).
      A dirty verdict with an empty content diff is "clean after
      refresh", never a block.
    * ``incoming_overwrite_paths`` — untracked paths the incoming target
      commits would overwrite. Those block a rebase even though untracked
      dirt is normally scratch: git would refuse mid-rebase, per-file.
    """

    mode_only_paths: frozenset[str] = frozenset()
    empty_diff_paths: frozenset[str] = frozenset()
    incoming_overwrite_paths: frozenset[str] = frozenset()


class HeadState(Enum):
    """Whether HEAD is attached to a branch or detached (or unknown)."""

    ATTACHED = "attached"
    DETACHED = "detached"
    UNKNOWN = "unknown"


class InProgressKind(Enum):
    """The kind of git operation currently in flight, if any.

    Every member except ``NONE`` names the authoritative marker (or
    marker directory) Git leaves in the per-worktree git directory:
    ``rebase-merge/`` for a sequencer-backed rebase, ``rebase-apply/``
    for the apply backend or an am-style patch application,
    ``MERGE_HEAD`` for a merge, ``CHERRY_PICK_HEAD`` / ``REVERT_HEAD``
    for the single-commit sequencer operations, and ``sequencer/`` for
    a multi-step cherry-pick or revert chain. The probe never invents a
    kind: an unreadable marker stays :meth:`Reading.unknown`.
    """

    NONE = "none"
    REBASE = "rebase"
    MERGE = "merge"
    CHERRY_PICK = "cherry_pick"
    REVERT = "revert"
    AM = "am"
    SEQUENCER = "sequencer"


class InProgressOwnership(Enum):
    """Who owns the in-flight git operation (independent of kind)."""

    OWNED = "owned"
    FOREIGN = "foreign"
    UNKNOWN = "unknown"


class IntegrationRecordState(Enum):
    """State of the durable integration record (if any)."""

    NONE = "none"
    CONSISTENT = "consistent"
    STALE = "stale"
    UNREADABLE = "unreadable"
    OLDER_VERSION = "older_version"
    FOREIGN = "foreign"


@dataclass(frozen=True, slots=True)
class Observation:
    """Frozen seven-dimension snapshot of integration state at a seam.

    Every dimension maps to an exhaustive closed vocabulary so the
    decision table can be walked as a total, pure function over the
    finite state space.
    """

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
        """True when any dimension caps an environmental reading as unknown."""
        return self._has_unknown


# ---------------------------------------------------------------------------
# The ONE worktree classifier.
# ---------------------------------------------------------------------------


class WorktreeDispatch(Enum):
    """What every gate must do with a worktree, given what the probe proved.

    ``UNKNOWN`` is a first-class member and is NOT ``BLOCKED``: an
    unreadable probe means "back off and read again", which is the
    difference between a transient I/O stall and a real conflict. Any
    design that collapses ``UNKNOWN`` into ``BLOCKED`` reproduces the
    commit loop (a timed-out ``git status`` read as "unresolved
    integration").
    """

    ALLOWED = "allowed"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class WorktreeVerdict:
    """The one answer every gate gives about a worktree.

    ``state`` is what was observed, ``is_clean`` is the dispatch/boundary
    cleanliness the classifier contract defines, ``dispatch`` is the
    three-valued action for a gate, ``reason`` is the operator-facing
    text derived from the SAME observation (never a stored string), and
    ``dirt_shape`` is the typed refinement of what kind of dirt the tree
    carries (``NONE`` for a clean tree). The shape defaults to ``NONE``
    only so legacy positional construction keeps working;
    :func:`classify_worktree` always sets it explicitly.
    """

    state: WorktreeState
    is_clean: bool
    dispatch: WorktreeDispatch
    reason: str
    dirt_shape: DirtShape = DirtShape.NONE

    @property
    def dispatch_allowed(self) -> bool:
        """True only for a provably dispatchable worktree."""
        return self.dispatch is WorktreeDispatch.ALLOWED

    @property
    def dispatch_blocked(self) -> bool:
        """True only when the probe PROVED a blocking fact."""
        return self.dispatch is WorktreeDispatch.BLOCKED

    @property
    def needs_reobservation(self) -> bool:
        """True when the probe could not answer and must be retried later."""
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
    """Return the frozen verdict the contract assigns to ``state``.

    This is the ONLY place a :class:`WorktreeState` becomes a dispatch
    verdict, so no two callers can drift apart. The unknown branch is
    built here (rather than being a constant) because it must carry the
    reason the probe actually failed with.
    """
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
    """Build the verdict for a KNOWN porcelain blob, refining it by shape.

    The base verdict comes from the one state table
    (:func:`_verdict_for_state`), so ``is_clean``/``dispatch`` keep their
    contract meaning; the shape then refines two cases the state alone
    cannot express:

    * ``LINE_ENDING_ONLY`` — a dirty-looking tree whose content diff is
      empty is clean after an index refresh: it never blocks a gate.
    * ``UNTRACKED_BLOCKING`` — untracked paths that incoming target
      commits would overwrite DO block (git refuses per-file, mid-rebase),
      unlike scratch untracked paths.
    * ``INDEX_FLAG_HIDDEN`` — a flagged path whose content differs from
      HEAD blocks exactly like ordinary tracked residue, even when the
      porcelain blob proves CLEAN. It outranks every refinement except
      ``UNMERGED`` (a real conflict stays the operation ladder's job).
    """
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
    """Classify ONE worktree observation into a state and a dispatch verdict.

    This is the single owner of "is this tree clean?" and "does this tree
    block dispatch?". The contract, for every :class:`WorktreeState`:

    =========================  ========  ======================
    observed state             is_clean  dispatch
    =========================  ========  ======================
    ``CLEAN``                  yes       ``ALLOWED``
    ``TRACKED_CHANGES``        no        ``BLOCKED`` (known)
    ``STAGED``                 no        ``BLOCKED`` (known)
    ``UNMERGED``               no        ``BLOCKED`` (known)
    ``UNTRACKED``              yes       ``ALLOWED``
    ``UNKNOWN``                no        ``UNKNOWN`` (back off)
    =========================  ========  ======================

    * ``UNTRACKED`` is clean FOR DISPATCH AND BOUNDARY PURPOSES. A
      scratch file left by a phase must never disable integration for
      every later seam; ``git rebase``/``git merge`` already refuse
      non-destructively and per-file for any untracked path they would
      overwrite. The state itself is still reported so the commit seam
      can package the file up.
    * ``BLOCKED`` is only ever reached from a KNOWN observation. A
      blocking fact is always repairable by the escalation ladder (commit
      the leftovers, resume or rebuild the owned operation), so no
      verdict here ever asks an operator for Git surgery.
    * An unreadable probe yields ``UNKNOWN``: never clean, never blocked.
      The caller backs off and reads again.

    ``observation`` is either the raw :class:`Reading` porcelain probe or
    a :class:`WorktreeState` a caller projected elsewhere. The state arm
    runs through :func:`_verdict_for_state`; the porcelain arm refines
    the shape per entry through :func:`_porcelain_to_shape`. ``evidence``
    is optional probe evidence (mode-only paths, empty content diffs,
    incoming overwrites) that refines the shape; it only ever narrows a
    block (``LINE_ENDING_ONLY`` is clean after refresh), never invents
    one without a dirty porcelain entry to hang it on.
    ``index_flag_dirt`` is the optional typed index-flag probe reading
    (:func:`read_index_flag_dirt`); flagged paths whose content differs
    from HEAD block as ``INDEX_FLAG_HIDDEN`` even when the porcelain
    blob is clean. A caller that cannot run the probe passes ``None``
    and keeps the legacy porcelain-only answer — which is exactly why
    every seam that may face a flagged tree must run the probe and treat
    its UNKNOWN reading as back-off, never as clean.
    """
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


# ---------------------------------------------------------------------------
# Three-valued probes. Each one returns a Reading; a non-zero exit, a
# timeout or an exception becomes ``unknown`` and NEVER an empty (i.e.
# "clean") reading.
# ---------------------------------------------------------------------------


def _unreadable(what: str, detail: str) -> Reading[str]:
    """Build the canonical unknown reading for a failed git probe."""
    return Reading[str].unknown(f"{what} unreadable: {detail}")


def read_worktree_porcelain(root: Path) -> Reading[str]:
    """Read ``git status --porcelain -z`` INCLUDING untracked entries.

    Untracked entries are observed on purpose: the classifier needs to
    tell "scratch files only" (clean for dispatch) from "tracked work"
    (blocking), and a probe run with ``--untracked-files=no`` cannot make
    that distinction — it silently answers "clean" about an untracked
    tree. The NUL-delimited form keeps a non-ASCII or newline-bearing
    path from arriving quoted and unopenable.

    A non-zero exit or a raised error yields
    :meth:`Reading.unknown`, never ``""``.
    """
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
    """Read whether ``HEAD`` is attached, detached, or unknown.

    ``git symbolic-ref --quiet HEAD`` exits 0 when ``HEAD`` is a symbolic
    ref (attached) and 1 when it is a direct SHA (detached). Any other
    exit — including "not a repository" and a killed or timed-out git —
    is :attr:`WorktreeDispatch`'s environmental sibling:
    :attr:`HeadState.UNKNOWN`. Reading it as "attached" is what let a
    detached commit seam report a successful commit.
    """
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
    """Read the merge base between two commits without losing failure semantics.

    The boolean distinguishes the three answers the ancestry projection needs:

    * ``(sha, True)`` — Git found a common ancestor.
    * ``(None, True)`` — Git proved the commits have no common ancestor.
    * ``(None, False)`` — Git could not answer, so the relationship is unknown.

    A failed probe must never be collapsed into "no common ancestor": the
    first is a divergence fact, while the second is an environmental
    unknown that must back off and re-observe.
    """
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
    """Return whether ``path`` exists, propagating unreadable-stat failures."""
    try:
        path.stat()
    except FileNotFoundError:
        return False
    return True


def _am_patch_counters_present(rebase_apply: Path) -> bool:
    """True when ``rebase_apply`` carries the ``next``/``last`` patch counters.

    ``git am`` and the rebase apply-backend share the ``rebase-apply``
    state directory, and there is no cheap marker that separates one
    from the other with certainty. Per git conventions the ``next`` and
    ``last`` counter files evidence an am-style patch application in
    progress, so a ``rebase-apply`` directory carrying both counters
    classifies as :attr:`InProgressKind.AM` while a counter-free
    directory keeps the legacy :attr:`InProgressKind.REBASE` answer.
    The distinction is decision-invariant: every non-``NONE`` kind maps
    to the same owned-resume and foreign-conflict decision rows, so the
    residual misclassification of an apply-backend rebase as ``AM``
    changes the label, never the action.
    """
    return _marker_present(rebase_apply / "next") and _marker_present(rebase_apply / "last")


#: Single-marker operation probes, most specific first: each entry pairs
#: one git-dir marker with the kind its presence proves. The rebase
#: markers are NOT in this table: ``rebase-merge/`` is checked outright
#: and ``rebase-apply/`` needs the am-vs-rebase counter distinction in
#: :func:`_am_patch_counters_present`.
_SINGLE_OPERATION_MARKERS: tuple[tuple[str, InProgressKind], ...] = (
    ("MERGE_HEAD", InProgressKind.MERGE),
    ("CHERRY_PICK_HEAD", InProgressKind.CHERRY_PICK),
    ("REVERT_HEAD", InProgressKind.REVERT),
    ("sequencer", InProgressKind.SEQUENCER),
)


def _read_operation_markers(git_dir: Path) -> Reading[InProgressKind]:
    """Classify the operation markers inside one worktree's git directory.

    The probe is evidence-based and most-specific-marker-first:
    ``rebase-merge/`` wins outright; a ``rebase-apply/`` directory with
    the ``next``/``last`` patch counters is an am-style application
    (see :func:`_am_patch_counters_present`) while a counter-free
    ``rebase-apply/`` keeps the legacy rebase answer; ``MERGE_HEAD``,
    ``CHERRY_PICK_HEAD`` and ``REVERT_HEAD`` name the single-operation
    markers; a bare ``sequencer/`` directory names a multi-step
    cherry-pick or revert chain. Only positive evidence produces a
    kind — an unreadable marker is :meth:`Reading.unknown`, never a
    positive classification.
    """
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
    """Read the authoritative Git marker for an in-progress operation.

    Git records an unfinished operation in the per-worktree git directory:
    ``rebase-merge/`` / ``rebase-apply/`` for a rebase, ``MERGE_HEAD``
    for a merge, ``CHERRY_PICK_HEAD`` / ``REVERT_HEAD`` for the
    single-commit sequencer operations, a ``rebase-apply/`` directory
    carrying the ``next``/``last`` patch counters for an am-style patch
    application, and ``sequencer/`` for a multi-step cherry-pick or
    revert chain. ``git rev-parse --absolute-git-dir`` already resolves
    to the per-worktree git directory (for a linked worktree that is
    ``.git/worktrees/<name>``), so probing the returned path never
    mistakes a sibling worktree's markers for this worktree's state.

    A failed git query or an unreadable marker is
    :meth:`Reading.unknown`; it is never positive evidence that an
    operation exists.
    """
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
    """Observe the worktree — porcelain plus the index-flag probe — and classify.

    The porcelain probe alone cannot see assume-unchanged / skip-worktree
    dirt (git itself hides it), so a confirmed-clean porcelain blob is
    never enough: the flag probe must also answer. An unreadable flag
    probe yields UNKNOWN — back off and re-observe — even when the
    porcelain blob proved clean; it is never evidence of a clean tree.
    """
    porcelain = read_worktree_porcelain(root)
    if not porcelain.is_known_good:
        return Reading[WorktreeVerdict].unknown(porcelain.reason or "worktree probe did not answer")
    # The flag probe runs through THIS module's ``run_git`` binding so a
    # caller that substitutes the observation seam's git runner (the
    # parity tests) intercepts the flag probe exactly like the other
    # probes; production passes the serialized, hardened runner through
    # the same binding unmodified.
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
    # Re-exported from :mod:`ralph.pipeline._porcelain_records` so the one
    # consumer that decodes porcelain paths keeps importing it from here.
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
