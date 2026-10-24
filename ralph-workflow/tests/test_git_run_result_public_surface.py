"""Contract tests for the public surface of ``ralph.git.git_run_result``.

The git outcome types are the CLOSED, TYPED vocabulary every git caller
reads instead of parsing stderr text. Their implementation lives in
dedicated modules (one public top-level class per module), but the PUBLIC
import path stays ``ralph.git.git_run_result`` -- that is the contract other
layers depend on, and this suite pins it:

* ``GitOutcomeKind`` keeps exactly its five members;
* ``GitOutcome`` stays a ``(kind, cause)`` pair;
* ``GitRunResult`` keeps its ``outcome_kind`` projection and its outcome
  invariants;
* the outcome factories keep producing the kinds they promise.
"""

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
    """Return a result carrying ``outcome`` (or the default outcome)."""
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
    """The outcome vocabulary is closed: five members, no more."""
    assert [member.value for member in GitOutcomeKind] == list(_EXPECTED_KINDS)
    assert GitOutcomeKind.SUCCEEDED == "succeeded"
    assert GitOutcomeKind.FAILED == "failed"
    assert GitOutcomeKind.TIMED_OUT == "timed_out"
    assert GitOutcomeKind.KILLED == "killed"
    assert GitOutcomeKind.UNKNOWN == "unknown"


def test_outcome_pairs_a_kind_with_its_cause() -> None:
    """The cause travels WITH the kind."""
    outcome = GitOutcome(GitOutcomeKind.FAILED, "nonzero_exit")
    assert outcome.kind is GitOutcomeKind.FAILED
    assert outcome.cause == "nonzero_exit"
    assert GitOutcome(GitOutcomeKind.SUCCEEDED).cause is None


def test_result_projects_the_carried_outcome_kind() -> None:
    """``outcome_kind`` is the closed projection of the carried outcome."""
    assert _result().outcome_kind is GitOutcomeKind.UNKNOWN
    assert _result(succeeded_outcome()).outcome_kind is GitOutcomeKind.SUCCEEDED
    assert _result(failed_outcome("nonzero_exit")).outcome_kind is GitOutcomeKind.FAILED
    assert _result(timed_out_outcome()).outcome_kind is GitOutcomeKind.TIMED_OUT
    assert _result(killed_outcome()).outcome_kind is GitOutcomeKind.KILLED
    assert _result(unknown_outcome()).outcome_kind is GitOutcomeKind.UNKNOWN


def test_result_rejects_an_impossible_outcome_pair() -> None:
    """A succeeded outcome carries no cause; a failed one always does."""
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
    """A failure without a cause is not a definite failure."""
    with pytest.raises(ValueError, match="non-empty cause"):
        failed_outcome("")
