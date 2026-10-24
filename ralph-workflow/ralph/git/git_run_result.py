"""GitRunResult: the typed result of one external git subprocess invocation.

The result carries a CLOSED, TYPED outcome union so that a caller never has
to parse stderr text (or a message string) to learn what happened to a git
call. :class:`GitOutcomeKind` has exactly five members:

* ``"succeeded"`` -- git exited with status 0.
* ``"failed"`` -- git exited non-zero for a DEFINITE cause (the cause is
  carried alongside the kind; a failed outcome ALWAYS names one).
* ``"timed_out"`` -- the call hit its deadline and was torn down.
* ``"killed"`` -- the call was force-killed (SIGKILL after the terminate
  grace, or an external signal death).
* ``"unknown"`` -- the result could not be determined. An indeterminate
  reading is NEVER reported as a failure: "I could not ask git" must not
  read as "git said no".

The outcome is represented as a ``(kind, cause)`` pair so the cause travels
WITH the kind -- a caller can never observe a ``"failed"`` outcome without
the reason that produced it, and can never observe a ``"succeeded"`` outcome
carrying a stale cause.

The two outcome TYPES live in their own modules
(:mod:`ralph.git._git_outcome_kind` and :mod:`ralph.git._git_outcome`) so
that every module in the git layer defines at most ONE public top-level
class; this module re-exports them, so ``from
ralph.git.git_run_result import GitOutcomeKind`` keeps working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ralph.git._git_outcome import GitOutcome
from ralph.git._git_outcome_kind import GitOutcomeKind

__all__ = [
    "GitOutcome",
    "GitOutcomeKind",
    "GitRunResult",
    "failed_outcome",
    "killed_outcome",
    "succeeded_outcome",
    "timed_out_outcome",
    "unknown_outcome",
]


def succeeded_outcome() -> GitOutcome:
    """Return the outcome of a git call that exited cleanly."""
    return GitOutcome(GitOutcomeKind.SUCCEEDED)


def failed_outcome(cause: str) -> GitOutcome:
    """Return the outcome of a git call that failed for a definite cause.

    Args:
        cause: Stable, machine-readable reason the call failed (for
            example ``"nonzero_exit"`` or ``"lock_contention"``). It MUST
            be non-empty: a failure without a cause is not a definite
            failure and must be reported as :func:`unknown_outcome`.

    Raises:
        ValueError: ``cause`` is empty.
    """
    if not cause:
        raise ValueError("failed_outcome requires a non-empty cause")
    return GitOutcome(GitOutcomeKind.FAILED, cause)


def timed_out_outcome(cause: str = "deadline_exceeded") -> GitOutcome:
    """Return the outcome of a git call that hit its deadline."""
    return GitOutcome(GitOutcomeKind.TIMED_OUT, cause)


def killed_outcome(cause: str = "force_kill") -> GitOutcome:
    """Return the outcome of a git call that was killed."""
    return GitOutcome(GitOutcomeKind.KILLED, cause)


def unknown_outcome(reason: str = "indeterminate") -> GitOutcome:
    """Return the outcome of a git call whose result is indeterminate."""
    return GitOutcome(GitOutcomeKind.UNKNOWN, reason)


@dataclass(frozen=True)
class GitRunResult:
    """Result of a git subprocess invocation, carrying a typed outcome.

    Attributes:
        args: The full argv the child was spawned with (``"git"`` first).
        returncode: The child's exit status. A negative value means the
            child died from a signal; ``-9`` is also the reported status
            for a child that never answered at all.
        stdout: The child's captured standard output.
        stderr: The child's captured standard error.
        outcome: The closed typed outcome union. It defaults to
            :attr:`GitOutcomeKind.UNKNOWN` with no cause -- an
            UNCLASSIFIED result reads as indeterminate, never as a failure
            and never as a success.
    """

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    outcome: GitOutcome | tuple[GitOutcomeKind, str | None] = field(
        default_factory=lambda: GitOutcome(GitOutcomeKind.UNKNOWN)
    )

    def __post_init__(self) -> None:
        """Reject outcome/cause pairs that cannot describe a real call."""
        kind_value, cause = self.outcome
        kind = GitOutcomeKind(kind_value)
        object.__setattr__(self, "outcome", GitOutcome(kind, cause))
        if kind is GitOutcomeKind.SUCCEEDED and cause is not None:
            raise ValueError("a succeeded outcome must not carry a cause")
        if kind is GitOutcomeKind.FAILED and not cause:
            raise ValueError("a failed outcome must carry a non-empty cause")

    @property
    def outcome_kind(self) -> GitOutcomeKind:
        """Return the closed outcome kind carried by this result."""
        kind, _cause = self.outcome
        return GitOutcomeKind(kind)
