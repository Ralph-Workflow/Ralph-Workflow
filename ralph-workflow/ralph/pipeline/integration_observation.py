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
:class:`WorktreeState` plus a :class:`WorktreeDispatch` verdict. The
clean/blocked question was previously answered by four or five
independent probes with four or five definitions; that disagreement is
what let one seam integrate while another refused. Every gate must call
:func:`classify_worktree` instead of re-deriving cleanliness.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final

from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS, parse_porcelain_z, unmerged_paths_z
from ralph.git.subprocess_runner import run_git

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class ReadingVariant(Enum):
    """Closed tag for :class:`Reading` variants."""

    KNOWN_GOOD = "known_good"
    KNOWN_BLOCKED = "known_blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
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


class HeadState(Enum):
    """Whether HEAD is attached to a branch or detached (or unknown)."""

    ATTACHED = "attached"
    DETACHED = "detached"
    UNKNOWN = "unknown"


class InProgressKind(Enum):
    """The kind of git operation currently in flight, if any."""

    NONE = "none"
    REBASE = "rebase"
    MERGE = "merge"
    CHERRY_PICK = "cherry_pick"


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


@dataclass(frozen=True)
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

    def has_unknown(self) -> bool:
        """True when any dimension caps an environmental reading as unknown."""
        return (
            self.target_vs_feature is TargetVsFeature.UNKNOWN
            or self.target_vs_remote is TargetVsRemote.UNKNOWN
            or self.worktree is WorktreeState.UNKNOWN
            or self.head is HeadState.UNKNOWN
            or self.in_progress_ownership is InProgressOwnership.UNKNOWN
        )


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


@dataclass(frozen=True)
class WorktreeVerdict:
    """The one answer every gate gives about a worktree.

    ``state`` is what was observed, ``is_clean`` is the dispatch/boundary
    cleanliness the classifier contract defines, ``dispatch`` is the
    three-valued action for a gate, and ``reason`` is the operator-facing
    text derived from the SAME observation (never a stored string).
    """

    state: WorktreeState
    is_clean: bool
    dispatch: WorktreeDispatch
    reason: str

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


#: Porcelain v1 status code for an untracked path.
_UNTRACKED_CODE: Final[str] = "??"

#: Porcelain v1 status code for an ignored path. ``git status`` only emits
#: it under ``--ignored``; it is skipped so an ignored build artefact can
#: never be mistaken for work.
_IGNORED_CODE: Final[str] = "!!"

#: First (``X``) status codes that mean "staged into the index".
_INDEX_STATUS_CODES: Final[frozenset[str]] = frozenset({"M", "A", "D", "R", "C", "T"})

#: Second (``Y``) status codes that mean "modified in the worktree".
_WORKTREE_STATUS_CODES: Final[frozenset[str]] = frozenset({"M", "D", "T"})

_CLEAN: Final[WorktreeVerdict] = WorktreeVerdict(
    state=WorktreeState.CLEAN,
    is_clean=True,
    dispatch=WorktreeDispatch.ALLOWED,
    reason="worktree clean",
)
_TRACKED_CHANGES: Final[WorktreeVerdict] = WorktreeVerdict(
    state=WorktreeState.TRACKED_CHANGES,
    is_clean=False,
    dispatch=WorktreeDispatch.BLOCKED,
    reason="uncommitted tracked changes in the worktree",
)
_STAGED: Final[WorktreeVerdict] = WorktreeVerdict(
    state=WorktreeState.STAGED,
    is_clean=False,
    dispatch=WorktreeDispatch.BLOCKED,
    reason="staged changes waiting to be committed",
)
_UNMERGED: Final[WorktreeVerdict] = WorktreeVerdict(
    state=WorktreeState.UNMERGED,
    is_clean=False,
    dispatch=WorktreeDispatch.BLOCKED,
    reason="unmerged paths remain from an unfinished rebase or merge",
)
_UNTRACKED: Final[WorktreeVerdict] = WorktreeVerdict(
    state=WorktreeState.UNTRACKED,
    is_clean=True,
    dispatch=WorktreeDispatch.ALLOWED,
    reason="untracked scratch paths only; they never block a gate",
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
    )


def _porcelain_to_state(text: str) -> WorktreeState:
    """Map one porcelain v1 blob onto a :class:`WorktreeState`.

    Accepts both the line-oriented and the NUL-delimited form: the
    canonical parser in :mod:`ralph.git.hardening` splits on ``\\0`` and
    every line-oriented record has the same ``XY<space>path`` shape, so
    newline-to-NUL normalisation reuses the one parser that already owns
    the unmerged-code table.
    """
    blob = text.replace("\n", "\0")
    if unmerged_paths_z(blob):
        return WorktreeState.UNMERGED
    has_staged = False
    has_tracked = False
    has_untracked = False
    for entry in parse_porcelain_z(blob):
        code = entry.xy
        if code == _UNTRACKED_CODE:
            has_untracked = True
            continue
        if code == _IGNORED_CODE:
            continue
        if code[0] in _INDEX_STATUS_CODES:
            has_staged = True
        if code[1] in _WORKTREE_STATUS_CODES:
            has_tracked = True
    if has_tracked:
        return WorktreeState.TRACKED_CHANGES
    if has_staged:
        return WorktreeState.STAGED
    if has_untracked:
        return WorktreeState.UNTRACKED
    return WorktreeState.CLEAN


def classify_worktree(observation: Reading[str] | WorktreeState) -> WorktreeVerdict:
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
    a :class:`WorktreeState` a caller projected elsewhere. Both arms run
    through :func:`_verdict_for_state`, so the two can never disagree.
    """
    if isinstance(observation, WorktreeState):
        return _verdict_for_state(observation)
    if not observation.is_known_good:
        return WorktreeVerdict(
            state=WorktreeState.UNKNOWN,
            is_clean=False,
            dispatch=WorktreeDispatch.UNKNOWN,
            reason=observation.reason or "worktree probe did not answer",
        )
    return _verdict_for_state(_porcelain_to_state(observation.value or ""))


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


def read_worktree(root: Path) -> Reading[WorktreeVerdict]:
    """Observe the worktree and classify it through the one classifier."""
    porcelain = read_worktree_porcelain(root)
    if not porcelain.is_known_good:
        return Reading[WorktreeVerdict].unknown(
            porcelain.reason or "worktree probe did not answer"
        )
    return Reading.known(classify_worktree(porcelain))


__all__ = [
    "HeadState",
    "InProgressKind",
    "InProgressOwnership",
    "IntegrationRecordState",
    "Observation",
    "Reading",
    "ReadingVariant",
    "TargetVsFeature",
    "TargetVsRemote",
    "WorktreeDispatch",
    "WorktreeState",
    "WorktreeVerdict",
    "classify_worktree",
    "read_head_state",
    "read_worktree",
    "read_worktree_porcelain",
    "reading_from_callable",
]
