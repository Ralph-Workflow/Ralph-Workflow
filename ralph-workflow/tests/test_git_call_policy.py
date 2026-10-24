"""Contract tests for the git call-class policy table and outcome classification.

These tests pin the properties the integration path depends on:

* the policy table is a SMALL table of call classes (not a per-command
  explosion) and every lookup over it is pure;
* the quarantine rung is a pure decision over the durable attempt count --
  "a command that has timed out twice against the same observed state is
  not run a third time";
* the classification of a call is a closed typed union: an ambiguous
  reading is ``"unknown"`` and NEVER ``"failed"``;
* the production runner threads a call class into the queue, so the table's
  deadline and termination grace are the values the child actually receives.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.git import git_command_queue
from ralph.git.git_call_policy import (
    GIT_CALL_POLICIES,
    GitCallClass,
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
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.process.manager import ProcessTerminationError
from ralph.timeout_defaults import GIT_TERMINATE_GRACE_SECONDS

if TYPE_CHECKING:
    from collections.abc import Sequence

_EXPECTED_CALL_CLASSES: tuple[GitCallClass, ...] = (
    "read_only_verify",
    "mutation",
    "network",
)
_OUTCOME_KINDS = {"succeeded", "failed", "timed_out", "killed", "unknown"}


def test_policy_table_is_a_small_table_of_call_classes() -> None:
    """The table has one row per call class -- never one row per command."""
    assert set(GIT_CALL_POLICIES) == set(_EXPECTED_CALL_CLASSES)
    for call_class, policy in GIT_CALL_POLICIES.items():
        assert policy.call_class == call_class
        assert policy.deadline_seconds > 0.0
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


def test_terminate_grace_has_one_numeric_source() -> None:
    """Every policy projects the live cleanup floor; no row stores a rival."""
    assert "terminate_grace_seconds" not in GitCallPolicy.__dataclass_fields__
    for call_class in _EXPECTED_CALL_CLASSES:
        assert policy_for(call_class).terminate_grace_seconds == GIT_TERMINATE_GRACE_SECONDS
        assert effective_terminate_grace_seconds(call_class) == GIT_TERMINATE_GRACE_SECONDS
    assert effective_terminate_grace_seconds() == GIT_TERMINATE_GRACE_SECONDS


def test_quarantine_rung_blocks_the_third_same_state_run() -> None:
    """Two same-state attempts exhaust the rung; the third run is quarantined."""
    assert quarantine_reason("mutation", "state-a", same_state_attempts=0) is None
    assert quarantine_reason("mutation", "state-a", same_state_attempts=1) is None
    assert (
        quarantine_reason("mutation", "state-a", same_state_attempts=2)
        == "same_state_timeout_threshold"
    )


def test_quarantine_rung_ignores_a_negative_attempt_count() -> None:
    """A negative count is not evidence of anything."""
    assert quarantine_reason("mutation", "state-a", same_state_attempts=-5) is None


def test_quarantine_rung_requires_a_signature() -> None:
    """An empty signature cannot identify an observed state."""
    with pytest.raises(ValueError):
        quarantine_reason("mutation", "", same_state_attempts=2)


def test_classification_of_a_finished_call() -> None:
    """Every observed call maps onto exactly one member of the closed union."""
    assert classify_git_outcome(returncode=0).kind is GitOutcomeKind.SUCCEEDED
    assert classify_git_outcome(returncode=1).kind is GitOutcomeKind.FAILED
    assert classify_git_outcome(returncode=1).cause == "nonzero_exit"
    assert classify_git_outcome(returncode=1, cause="lock_contention") == GitOutcome(
        GitOutcomeKind.FAILED,
        "lock_contention",
    )
    assert classify_git_outcome(returncode=0, timed_out=True).kind is GitOutcomeKind.TIMED_OUT
    assert classify_git_outcome(returncode=0, killed=True).kind is GitOutcomeKind.KILLED
    assert classify_git_outcome(returncode=-9).kind is GitOutcomeKind.KILLED


def test_ambiguous_reading_is_never_reported_as_failed() -> None:
    """An indeterminate result reads as unknown, never as a git failure."""
    ambiguous = [
        classify_git_outcome(returncode=1, indeterminate=True),
        classify_git_exception(OSError("git vanished")),
        classify_git_exception(RuntimeError("boom")),
    ]
    assert {outcome.kind for outcome in ambiguous} == {GitOutcomeKind.UNKNOWN}


def test_exception_classification() -> None:
    """A timeout is a timeout, a kill is a kill, a non-zero exit is a failure."""
    assert classify_git_exception(subprocess.TimeoutExpired("git", 1.0)).kind is (
        GitOutcomeKind.TIMED_OUT
    )
    assert classify_git_exception(TimeoutError()).kind is GitOutcomeKind.TIMED_OUT
    assert classify_git_exception(ProcessTerminationError(1, 1)).kind is GitOutcomeKind.KILLED
    assert classify_git_exception(subprocess.CalledProcessError(1, "git")).kind is (
        GitOutcomeKind.FAILED
    )
    assert classify_git_exception(OSError("git vanished")).kind is GitOutcomeKind.UNKNOWN
    assert classify_git_exception(RuntimeError("boom")).kind is GitOutcomeKind.UNKNOWN


def test_outcome_union_is_closed_and_str_valued() -> None:
    """The enum exposes exactly the five documented string-valued members."""
    assert {member.value for member in GitOutcomeKind} == _OUTCOME_KINDS
    assert len(tuple(GitOutcomeKind)) == len(_OUTCOME_KINDS)
    assert isinstance(GitOutcomeKind.SUCCEEDED, str)
    assert str(GitOutcomeKind.SUCCEEDED) == "succeeded"
    assert GitOutcomeKind.FAILED.value == "failed"


def test_constructors_cover_exactly_the_union_members() -> None:
    """Every constructor produces one member of the canonical enum."""
    kinds = {
        succeeded_outcome().kind,
        failed_outcome("nonzero_exit").kind,
        timed_out_outcome().kind,
        killed_outcome().kind,
        unknown_outcome().kind,
    }
    assert kinds == set(GitOutcomeKind)


def test_git_run_result_carries_the_typed_outcome() -> None:
    """The result exposes the canonical member through ``outcome_kind``."""
    result = GitRunResult(args=("git", "status"), returncode=0, stdout="ok", stderr="")
    assert result.outcome_kind is GitOutcomeKind.UNKNOWN
    classified = replace(result, outcome=succeeded_outcome())
    assert classified.outcome_kind is GitOutcomeKind.SUCCEEDED
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
            outcome=GitOutcome(GitOutcomeKind.FAILED),
        )


def test_run_git_threads_call_class_to_the_production_queue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The public runner forwards the class that selects the policy row."""
    captured: dict[str, object] = {}

    def fake_route(
        args: Sequence[str],
        *,
        cwd: Path | None,
        label: str,
        options: GitRunOptions | None = None,
        call_class: GitCallClass | None = None,
    ) -> GitRunResult:
        captured["args"] = args
        captured["cwd"] = cwd
        captured["label"] = label
        captured["options"] = options
        captured["call_class"] = call_class
        return GitRunResult(args=("git", *args), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(git_command_queue, "route_run_git", fake_route)
    result = run_git(
        ("status", "--porcelain"),
        cwd=tmp_path,
        label="integration:status",
        call_class="read_only_verify",
    )

    assert captured["call_class"] == "read_only_verify"
    assert result.outcome_kind is GitOutcomeKind.UNKNOWN


def test_route_run_git_applies_the_call_class_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The queue applies the selected table deadline to the real spawn."""
    captured: dict[str, GitRunOptions | None] = {}

    def fake_spawn(
        args: Sequence[str],
        *,
        cwd: Path | None,
        label: str,
        options: GitRunOptions | None = None,
    ) -> GitRunResult:
        captured["options"] = options
        return GitRunResult(args=("git", *args), returncode=0, stdout="", stderr="")

    from ralph.git import subprocess_runner

    monkeypatch.setattr(subprocess_runner, "_spawn_git", fake_spawn)
    result = git_command_queue.route_run_git(
        ("status", "--porcelain"),
        cwd=tmp_path,
        label="integration:status",
        call_class="read_only_verify",
    )

    options = captured["options"]
    assert options is not None
    assert options.timeout == policy_for("read_only_verify").deadline_seconds
    assert options.call_class == "read_only_verify"
    assert result.outcome_kind is GitOutcomeKind.SUCCEEDED
