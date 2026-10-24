"""Call-class policy table, outcome classification and quarantine rung for git calls.

Every external git call Ralph makes belongs to exactly one CALL CLASS, and
the class -- not the individual git subcommand -- decides the operational
budget of that call. This module owns that table so the numbers live in ONE
place instead of being re-spelled at every call site:

* :data:`GIT_CALL_POLICIES` -- the table itself. It has three rows
  (``read_only_verify``, ``mutation``, ``network``); it is deliberately NOT
  a per-subcommand explosion.
* :func:`policy_for` -- the pure lookup over the table.
* :func:`effective_deadline_seconds` / :func:`effective_terminate_grace_seconds`
  -- the deadline and the SIGTERM-to-SIGKILL grace for one call class.
* :func:`quarantine_reason` -- the quarantine rung: "a command that has
  timed out twice against the same observed state is not run a third time".
* :func:`classify_git_outcome` / :func:`classify_git_exception` -- the
  classification of an observed call into the closed outcome union defined
  in :mod:`ralph.git.git_run_result`.

Every lookup in this module is PURE: it reads the table and returns a value.
No lookup mutates state, so a test can assert the table directly and the
pipeline can call it from a hot path without side effects.

The quarantine decision is deliberately expressed over a PLAIN integer --
"how many attempts have already been charged against this same observed
state" -- rather than over a ledger object. The durable attempt record
(:mod:`ralph.pipeline.integration_attempt_ledger`) lives one layer up, so
keeping the decision pure keeps this module free of a pipeline dependency
and lets the pipeline feed its own per-``(signature, action)`` repeat count
straight in.
"""

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


#: The closed set of git call classes. A call is classified by what it DOES
#: to the repository and the network, never by which git subcommand it uses.
GitCallClass = Literal["read_only_verify", "mutation", "network"]

#: How a call that overran its deadline is torn down.
#:
#: ``"terminate_then_kill"`` sends SIGTERM, waits out the terminate grace
#: (so git can remove its own lock files) and then escalates to SIGKILL.
#: ``"terminate_only"`` sends SIGTERM and waits, never escalating.
GitKillPolicy = Literal["terminate_then_kill", "terminate_only"]

#: Why a call was quarantined instead of being run.
GitQuarantineReason = Literal["same_state_timeout_threshold"]


@dataclass(frozen=True)
class GitCallPolicy:
    """The operational budget of one class of external git call.

    Attributes:
        call_class: The class this policy governs.
        deadline_seconds: Wall-clock ceiling for one attempt of the call.
        terminate_grace_seconds: How long a torn-down child may spend
            between SIGTERM and SIGKILL.
        kill_policy: Whether the teardown escalates to SIGKILL.
        quarantine_after_same_state_attempts: How many attempts against the
            SAME observed state exhaust the rung and quarantine the next
            run of that command.
    """

    call_class: GitCallClass
    deadline_seconds: float
    terminate_grace_seconds: float
    kill_policy: GitKillPolicy
    quarantine_after_same_state_attempts: int


#: The call-class policy table. Three rows, one per call class -- NOT one
#: row per git subcommand. The deadlines stay inside the repository-wide
#: ``GIT_SUBPROCESS_TIMEOUT_SECONDS`` ceiling so a call class can never
#: outrun the hard wall the runner documents.
GIT_CALL_POLICIES: Mapping[GitCallClass, GitCallPolicy] = {
    "read_only_verify": GitCallPolicy(
        call_class="read_only_verify",
        deadline_seconds=30.0,
        terminate_grace_seconds=5.0,
        kill_policy="terminate_then_kill",
        quarantine_after_same_state_attempts=2,
    ),
    "mutation": GitCallPolicy(
        call_class="mutation",
        deadline_seconds=120.0,
        terminate_grace_seconds=5.0,
        kill_policy="terminate_then_kill",
        quarantine_after_same_state_attempts=2,
    ),
    "network": GitCallPolicy(
        call_class="network",
        deadline_seconds=120.0,
        terminate_grace_seconds=10.0,
        kill_policy="terminate_then_kill",
        quarantine_after_same_state_attempts=2,
    ),
}


def _validate_policy_table() -> None:
    """Reject a policy row that could not bound a call (import-time guard)."""
    for policy in GIT_CALL_POLICIES.values():
        if policy.deadline_seconds <= 0.0:
            raise RuntimeError(
                f"git call policy {policy.call_class!r} needs a positive deadline; "
                f"got {policy.deadline_seconds!r}"
            )
        if policy.terminate_grace_seconds <= 0.0:
            raise RuntimeError(
                f"git call policy {policy.call_class!r} needs a positive terminate grace; "
                f"got {policy.terminate_grace_seconds!r}"
            )
        if policy.quarantine_after_same_state_attempts < 1:
            raise RuntimeError(
                f"git call policy {policy.call_class!r} needs a quarantine threshold of at "
                f"least 1; got {policy.quarantine_after_same_state_attempts!r}"
            )


_validate_policy_table()


def policy_for(call_class: GitCallClass) -> GitCallPolicy:
    """Return the policy that governs ``call_class``.

    Args:
        call_class: One of :data:`GitCallClass`.

    Returns:
        The :class:`GitCallPolicy` row for that class.

    Raises:
        KeyError: ``call_class`` is not a member of the closed class set.
    """
    return GIT_CALL_POLICIES[call_class]


def effective_deadline_seconds(
    call_class: GitCallClass,
    requested: float | None = None,
) -> float:
    """Return the wall-clock deadline for one call of ``call_class``.

    Args:
        call_class: The class of the call.
        requested: An explicit deadline from the caller. When given it
            always wins -- the table supplies the default, it never
            overrides an explicit request.

    Returns:
        The deadline in seconds.
    """
    if requested is not None:
        return requested
    return policy_for(call_class).deadline_seconds


def effective_terminate_grace_seconds(call_class: GitCallClass) -> float:
    """Return the SIGTERM-to-SIGKILL grace for one call of ``call_class``.

    Args:
        call_class: The class of the call.

    Returns:
        The terminate grace in seconds.
    """
    return policy_for(call_class).terminate_grace_seconds


def quarantine_reason(
    *,
    same_state_attempts: int,
    call_class: GitCallClass,
) -> GitQuarantineReason | None:
    """Return why a call must be quarantined, or ``None`` when it may run.

    The quarantine rung is the durable rule "a command that has timed out
    twice against the same observed state is not run a third time". The
    decision is a pure function of the durable attempt record: the caller
    passes how many attempts have already been charged against the SAME
    observed state (the durable attempt ledger's per-``(signature, action)``
    repeat count) and this module decides whether the next run is allowed.

    Args:
        same_state_attempts: How many attempts have already been charged
            against the same observed state. Values below zero are treated
            as zero -- a negative count is not evidence of anything.
        call_class: The class of the call being considered.

    Returns:
        The quarantine reason when the rung is exhausted, else ``None``
        (the call may run).
    """
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
    """Classify one finished external git call into the closed outcome union.

    The classification order is the fail-closed order: an indeterminate
    reading wins over everything, then a deadline overrun, then a kill, then
    the exit status. A non-zero exit is a DEFINITE failure and is reported
    as ``"failed"`` with its cause; an unreadable or indeterminate reading
    is reported as ``"unknown"`` and NEVER as ``"failed"``.

    Args:
        returncode: The child's exit status. A negative value means the
            child died from a signal (``-9`` also stands for a child that
            never answered at all).
        timed_out: The call overran its deadline.
        killed: The call was force-killed during teardown.
        indeterminate: The result could not be read at all.
        cause: A stable cause for the failure, when the caller knows one.

    Returns:
        The typed outcome of the call.
    """
    if indeterminate:
        return unknown_outcome(cause or "indeterminate")
    if timed_out:
        return timed_out_outcome(cause or "deadline_exceeded")
    if killed:
        return killed_outcome(cause or "force_kill")
    if returncode == 0:
        return succeeded_outcome()
    if returncode < 0:
        # A signal death (SIGKILL included) is a kill, not a git failure.
        return killed_outcome(cause or f"signal_{abs(returncode)}")
    return failed_outcome(cause or "nonzero_exit")


def classify_git_exception(exc: BaseException) -> GitOutcome:
    """Classify an exception raised by an external git call.

    A timeout is a timeout even when it arrives wrapped, a non-zero exit is
    a definite failure, and everything else -- a vanished working directory,
    a missing git binary, an unreadable pipe -- is INDETERMINATE and is
    reported as ``"unknown"`` so no caller can mistake it for a git
    failure.

    Args:
        exc: The exception that escaped the git call.

    Returns:
        The typed outcome of the failed call.
    """
    if isinstance(exc, subprocess.TimeoutExpired | TimeoutError):
        return timed_out_outcome()
    if isinstance(exc, subprocess.CalledProcessError):
        return failed_outcome("nonzero_exit")
    if isinstance(exc, GitCommandError):
        return failed_outcome("nonzero_exit")
    if isinstance(exc, OSError):
        return unknown_outcome("os_error")
    return unknown_outcome("unclassified_exception")
