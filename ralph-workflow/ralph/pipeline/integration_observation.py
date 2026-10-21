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
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


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
    "WorktreeState",
    "reading_from_callable",
]
