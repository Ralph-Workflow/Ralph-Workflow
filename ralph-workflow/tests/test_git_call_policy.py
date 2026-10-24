"""Contract tests for the git call-class policy table and outcome classification.

These tests pin the three properties the integration path depends on:

* the policy table is a SMALL table of call classes (not a per-command
  explosion) and every lookup over it is pure;
* the quarantine rung is a pure decision over the durable attempt count --
  "a command that has timed out twice against the same observed state is
  not run a third time";
* the classification of a call is a closed typed union: an ambiguous
  reading is ``"unknown"`` and NEVER ``"failed"``.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace

import pytest

from ralph.git.git_call_policy import (
    GIT_CALL_POLICIES,
    GitCallPolicy,
    classify_git_exception,
    classify_git_outcome,
    effective_deadline_seconds,
    effective_terminate_grace_seconds,
    policy_for,
    quarantine_reason,
)
from ralph.git.git_run_result import (
    GitOutcome,
    GitOutcomeKind,
    GitRunResult,
    failed_outcome,
    killed_outcome,
    succeeded_outcome,
    timed_out_outcome,
    unknown_outcome,
)

_EXPECTED_CALL_CLASSES = {"read_only_verify", "mutation", "network"}
_OUTCOME_KINDS = {"succeeded", "failed", "timed_out", "killed", "unknown"}


def test_policy_table_is_a_small_table_of_call_classes() -> None:
    """The table has one row per call class -- never one row per command."""
    assert set(GIT_CALL_POLICIES) == _EXPECTED_CALL_CLASSES
    for call_class, policy in GIT_CALL_POLICIES.items():
        assert policy.call_class == call_class
        assert policy.deadline_seconds > 0.0
        assert policy.terminate_grace_seconds > 0.0
        assert policy.kill_policy in {"terminate_then_kill", "terminate_only"}
        assert policy.quarantine_after_same_state_attempts >= 1


def test_policy_for_is_a_pure_lookup() -> None:
    """Looking a class up twice yields the same row and changes nothing."""
    first = policy_for("mutation")
    second = policy_for("mutation")
    assert first is second
    assert isinstance(first, GitCallPolicy)


def test_effective_deadline_prefers_an_explicit_request() -> None:
    """The table supplies the default; an explicit request always wins."""
    policy = policy_for("read_only_verify")
    assert effective_deadline_seconds("read_only_verify") == policy.deadline_seconds
    assert effective_deadline_seconds("read_only_verify", 7.5) == 7.5
    assert (
        effective_terminate_grace_seconds("network")
        == policy_for("network").terminate_grace_seconds
    )


def test_quarantine_rung_blocks_the_third_same_state_run() -> None:
    """Two same-state attempts exhaust the rung; the third run is quarantined."""
    assert quarantine_reason(same_state_attempts=0, call_class="mutation") is None
    assert quarantine_reason(same_state_attempts=1, call_class="mutation") is None
    assert (
        quarantine_reason(same_state_attempts=2, call_class="mutation")
        == "same_state_timeout_threshold"
    )


def test_quarantine_rung_ignores_a_negative_attempt_count() -> None:
    """A negative count is not evidence of anything."""
    assert quarantine_reason(same_state_attempts=-5, call_class="mutation") is None


def test_classification_of_a_finished_call() -> None:
    """Every observed call maps onto exactly one variant of the union."""
    assert classify_git_outcome(returncode=0) == ("succeeded", None)
    assert classify_git_outcome(returncode=1) == ("failed", "nonzero_exit")
    assert classify_git_outcome(returncode=1, cause="lock_contention") == (
        "failed",
        "lock_contention",
    )
    assert classify_git_outcome(returncode=0, timed_out=True) == (
        "timed_out",
        "deadline_exceeded",
    )
    assert classify_git_outcome(returncode=0, killed=True) == ("killed", "force_kill")
    assert classify_git_outcome(returncode=-9) == ("killed", "signal_9")


def test_ambiguous_reading_is_never_reported_as_failed() -> None:
    """An indeterminate result reads as unknown, never as a git failure."""
    ambiguous = [
        classify_git_outcome(returncode=1, indeterminate=True),
        classify_git_exception(OSError("git vanished")),
        classify_git_exception(RuntimeError("boom")),
    ]
    assert {outcome[0] for outcome in ambiguous} == {"unknown"}


def test_exception_classification() -> None:
    """A timeout is a timeout, a non-zero exit is a failure, the rest is unknown."""
    assert classify_git_exception(subprocess.TimeoutExpired("git", 1.0))[0] == "timed_out"
    assert classify_git_exception(TimeoutError())[0] == "timed_out"
    assert classify_git_exception(subprocess.CalledProcessError(1, "git"))[0] == "failed"
    assert classify_git_exception(OSError("git vanished"))[0] == "unknown"
    assert classify_git_exception(RuntimeError("boom"))[0] == "unknown"


def test_outcome_union_is_closed() -> None:
    """The constructors cover exactly the five documented variants."""
    kinds = {
        succeeded_outcome()[0],
        failed_outcome("nonzero_exit")[0],
        timed_out_outcome()[0],
        killed_outcome()[0],
        unknown_outcome()[0],
    }
    assert kinds == _OUTCOME_KINDS


def test_git_run_result_carries_the_typed_outcome() -> None:
    """The result carries the union; an unclassified result is unknown."""
    result = GitRunResult(args=("git", "status"), returncode=0, stdout="ok", stderr="")
    assert result.outcome == ("unknown", None)
    classified = replace(result, outcome=succeeded_outcome())
    assert classified.outcome[0] == "succeeded"
    assert classified.args == ("git", "status")


def test_a_failed_outcome_always_names_a_cause() -> None:
    """A failure without a cause is not a definite failure."""
    with pytest.raises(ValueError):
        failed_outcome("")
    with pytest.raises(ValueError):
        GitRunResult(
            args=("git",),
            returncode=1,
            stdout="",
            stderr="",
            outcome=("failed", None),
        )


def test_outcome_kind_alias_matches_the_constructed_variants() -> None:
    """The kind alias and the constructors agree on the closed variant set."""
    kind: GitOutcomeKind = succeeded_outcome()[0]
    outcome: GitOutcome = timed_out_outcome()
    assert kind == "succeeded"
    assert outcome[0] == "timed_out"
