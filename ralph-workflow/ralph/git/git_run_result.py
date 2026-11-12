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
    return GitOutcome(GitOutcomeKind.SUCCEEDED)


def failed_outcome(cause: str) -> GitOutcome:
    if not cause:
        raise ValueError("failed_outcome requires a non-empty cause")
    return GitOutcome(GitOutcomeKind.FAILED, cause)


def timed_out_outcome(cause: str = "deadline_exceeded") -> GitOutcome:
    return GitOutcome(GitOutcomeKind.TIMED_OUT, cause)


def killed_outcome(cause: str = "force_kill") -> GitOutcome:
    return GitOutcome(GitOutcomeKind.KILLED, cause)


def unknown_outcome(reason: str = "indeterminate") -> GitOutcome:
    return GitOutcome(GitOutcomeKind.UNKNOWN, reason)


@dataclass(frozen=True)
class GitRunResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    outcome: GitOutcome | tuple[GitOutcomeKind, str | None] = field(
        default_factory=lambda: GitOutcome(GitOutcomeKind.UNKNOWN)
    )

    def __post_init__(self) -> None:
        kind_value, cause = self.outcome
        kind = GitOutcomeKind(kind_value)
        object.__setattr__(self, "outcome", GitOutcome(kind, cause))
        if kind is GitOutcomeKind.SUCCEEDED and cause is not None:
            raise ValueError("a succeeded outcome must not carry a cause")
        if kind is GitOutcomeKind.FAILED and not cause:
            raise ValueError("a failed outcome must carry a non-empty cause")

    @property
    def outcome_kind(self) -> GitOutcomeKind:
        kind, _cause = self.outcome
        return GitOutcomeKind(kind)
