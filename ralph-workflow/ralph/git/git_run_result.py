from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.git._git_outcome import GitOutcome
from ralph.git._git_outcome_kind import GitOutcomeKind

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "GIT_TIMEOUT_RETURNCODE",
    "GitOutcome",
    "GitOutcomeKind",
    "GitRunResult",
    "failed_outcome",
    "killed_outcome",
    "succeeded_outcome",
    "summarize_command",
    "timed_out_outcome",
    "timed_out_result",
    "unknown_outcome",
]

GIT_TIMEOUT_RETURNCODE: int = 124
_SUMMARY_KEPT_ARGS: int = 4


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


def summarize_command(cmd: Sequence[str]) -> str:
    kept = " ".join(cmd[:_SUMMARY_KEPT_ARGS])
    hidden = len(cmd) - _SUMMARY_KEPT_ARGS
    return f"{kept} (+{hidden} more)" if hidden > 0 else kept


def timed_out_result(cmd: Sequence[str], timeout: float) -> GitRunResult:
    return GitRunResult(
        args=tuple(cmd),
        returncode=GIT_TIMEOUT_RETURNCODE,
        stdout="",
        stderr=f"'{summarize_command(cmd)}' timed out after {timeout:g} seconds",
        outcome=timed_out_outcome(),
    )
