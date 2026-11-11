from __future__ import annotations

import pytest

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

_EXPECTED_KINDS = ("succeeded", "failed", "timed_out", "killed", "unknown")


def _result(outcome: GitOutcome | None = None) -> GitRunResult:
    if outcome is None:
        return GitRunResult(args=("git", "status"), returncode=0, stdout="", stderr="")
    return GitRunResult(
        args=("git", "status"),
        returncode=0,
        stdout="",
        stderr="",
        outcome=outcome,
    )


def test_outcome_kind_keeps_its_five_members() -> None:
    assert [member.value for member in GitOutcomeKind] == list(_EXPECTED_KINDS)
    assert GitOutcomeKind.SUCCEEDED == "succeeded"
    assert GitOutcomeKind.FAILED == "failed"
    assert GitOutcomeKind.TIMED_OUT == "timed_out"
    assert GitOutcomeKind.KILLED == "killed"
    assert GitOutcomeKind.UNKNOWN == "unknown"


def test_outcome_pairs_a_kind_with_its_cause() -> None:
    outcome = GitOutcome(GitOutcomeKind.FAILED, "nonzero_exit")
    assert outcome.kind is GitOutcomeKind.FAILED
    assert outcome.cause == "nonzero_exit"
    assert GitOutcome(GitOutcomeKind.SUCCEEDED).cause is None


def test_result_projects_the_carried_outcome_kind() -> None:
    assert _result().outcome_kind is GitOutcomeKind.UNKNOWN
    assert _result(succeeded_outcome()).outcome_kind is GitOutcomeKind.SUCCEEDED
    assert _result(failed_outcome("nonzero_exit")).outcome_kind is GitOutcomeKind.FAILED
    assert _result(timed_out_outcome()).outcome_kind is GitOutcomeKind.TIMED_OUT
    assert _result(killed_outcome()).outcome_kind is GitOutcomeKind.KILLED
    assert _result(unknown_outcome()).outcome_kind is GitOutcomeKind.UNKNOWN


def test_result_rejects_an_impossible_outcome_pair() -> None:
    with pytest.raises(ValueError, match="succeeded outcome must not carry a cause"):
        GitRunResult(
            args=("git", "status"),
            returncode=0,
            stdout="",
            stderr="",
            outcome=GitOutcome(GitOutcomeKind.SUCCEEDED, "stale"),
        )
    with pytest.raises(ValueError, match="failed outcome must carry a non-empty cause"):
        GitRunResult(
            args=("git", "status"),
            returncode=1,
            stdout="",
            stderr="",
            outcome=GitOutcome(GitOutcomeKind.FAILED),
        )


def test_failed_outcome_requires_a_non_empty_cause() -> None:
    with pytest.raises(ValueError, match="non-empty cause"):
        failed_outcome("")
