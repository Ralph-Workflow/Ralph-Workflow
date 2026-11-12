from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from git.exc import GitCommandError

from ralph.git.git_run_result import (
    GitOutcome,
    failed_outcome,
    killed_outcome,
    succeeded_outcome,
    timed_out_outcome,
    unknown_outcome,
)
from ralph.process.manager import ProcessTerminationError
from ralph.timeout_defaults import (
    GIT_SUBPROCESS_TIMEOUT_SECONDS,
    GIT_TERMINATE_GRACE_SECONDS,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "GIT_CALL_POLICIES",
    "GitCallClass",
    "GitCallPolicy",
    "GitKillPolicy",
    "GitQuarantineReason",
    "classify_git_exception",
    "classify_git_outcome",
    "effective_deadline_seconds",
    "effective_terminate_grace_seconds",
    "policy_for",
    "quarantine_reason",
]


GitCallClass = Literal["read_only_verify", "mutation", "network"]

GitKillPolicy = Literal["terminate_then_kill", "terminate_only"]

GitQuarantineReason = Literal["same_state_timeout_threshold"]


@dataclass(frozen=True)
class GitCallPolicy:
    call_class: GitCallClass
    deadline_seconds: float
    kill_policy: GitKillPolicy
    quarantine_after_same_state_attempts: int

    @property
    def terminate_grace_seconds(self) -> float:
        return GIT_TERMINATE_GRACE_SECONDS


GIT_CALL_POLICIES: Mapping[GitCallClass, GitCallPolicy] = {
    "read_only_verify": GitCallPolicy(
        call_class="read_only_verify",
        deadline_seconds=30.0,
        kill_policy="terminate_then_kill",
        quarantine_after_same_state_attempts=2,
    ),
    "mutation": GitCallPolicy(
        call_class="mutation",
        deadline_seconds=120.0,
        kill_policy="terminate_then_kill",
        quarantine_after_same_state_attempts=2,
    ),
    "network": GitCallPolicy(
        call_class="network",
        deadline_seconds=120.0,
        kill_policy="terminate_then_kill",
        quarantine_after_same_state_attempts=2,
    ),
}


def _validate_policy_table() -> None:
    for policy in GIT_CALL_POLICIES.values():
        if policy.deadline_seconds <= 0.0:
            raise RuntimeError(
                f"git call policy {policy.call_class!r} needs a positive deadline; "
                f"got {policy.deadline_seconds!r}"
            )
        if policy.deadline_seconds > GIT_SUBPROCESS_TIMEOUT_SECONDS:
            raise RuntimeError(
                f"git call policy {policy.call_class!r} exceeds the repository-wide "
                f"timeout ceiling; got {policy.deadline_seconds!r}"
            )
        if policy.quarantine_after_same_state_attempts < 1:
            raise RuntimeError(
                f"git call policy {policy.call_class!r} needs a quarantine threshold of at "
                f"least 1; got {policy.quarantine_after_same_state_attempts!r}"
            )


_validate_policy_table()


def policy_for(call_class: GitCallClass) -> GitCallPolicy:
    return GIT_CALL_POLICIES[call_class]


def effective_deadline_seconds(
    call_class: GitCallClass,
    requested: float | None = None,
) -> float:
    if requested is not None:
        return requested
    return policy_for(call_class).deadline_seconds


def effective_terminate_grace_seconds(call_class: GitCallClass | None = None) -> float:
    if call_class is None:
        return GIT_TERMINATE_GRACE_SECONDS
    return policy_for(call_class).terminate_grace_seconds


def quarantine_reason(
    call_class: GitCallClass,
    signature: str,
    *,
    same_state_attempts: int,
) -> GitQuarantineReason | None:
    if not signature:
        raise ValueError("quarantine_reason requires a non-empty signature")
    threshold = policy_for(call_class).quarantine_after_same_state_attempts
    if max(0, same_state_attempts) >= threshold:
        return "same_state_timeout_threshold"
    return None


def classify_git_outcome(
    *,
    returncode: int,
    timed_out: bool = False,
    killed: bool = False,
    indeterminate: bool = False,
    cause: str | None = None,
) -> GitOutcome:
    if indeterminate:
        return unknown_outcome(cause or "indeterminate")
    if timed_out:
        return timed_out_outcome(cause or "deadline_exceeded")
    if killed:
        return killed_outcome(cause or "force_kill")
    if returncode == 0:
        return succeeded_outcome()
    if returncode < 0:
        return killed_outcome(cause or f"signal_{abs(returncode)}")
    return failed_outcome(cause or "nonzero_exit")


def classify_git_exception(exc: BaseException) -> GitOutcome:
    if isinstance(exc, subprocess.TimeoutExpired | TimeoutError):
        return timed_out_outcome()
    if isinstance(exc, ProcessTerminationError):
        return killed_outcome()
    if isinstance(exc, subprocess.CalledProcessError):
        return failed_outcome("nonzero_exit")
    if isinstance(exc, GitCommandError):
        return failed_outcome("nonzero_exit")
    if isinstance(exc, OSError):
        return unknown_outcome("os_error")
    return unknown_outcome("unclassified_exception")
