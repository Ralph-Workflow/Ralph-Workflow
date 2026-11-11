"""Boundary behaviour of the commit-review outcome decision.

The commit-review phase advances to the commit phase only when every
requested action either applied or was a genuinely safe no-op. One failed
apply operation therefore has to loop the agent back, even when other
actions of the same batch applied successfully.

The cases below exercise ``decide_commit_review_outcome`` directly: they
need no git repository and no filesystem, so they belong to the default
lane.
"""

from __future__ import annotations

from ralph.phases.commit_review import (
    CommitReviewApplyReport,
    CommitReviewLoopback,
    decide_commit_review_outcome,
)

_PHASE_NAME = "development_commit_review"


def _loopback_hint(report: CommitReviewApplyReport) -> str:
    """Return the loopback hint the report produces."""
    outcome = decide_commit_review_outcome(_PHASE_NAME, report)
    assert isinstance(outcome, CommitReviewLoopback), (
        f"Expected a loopback for report {report!r}, got {outcome!r}"
    )
    return outcome.hint


def test_failed_delete_beside_applied_delete_loops_back() -> None:
    """A failed delete alongside a successful delete must loop the agent back.

    The batch applied one delete and failed another, so the requested edit
    set is incomplete: the commit phase must not run on a half-applied
    cleanup.
    """
    report = CommitReviewApplyReport(
        applied_delete_paths=["temp.tmp"],
        failed_delete_paths=["locked.tmp"],
    )

    hint = _loopback_hint(report)

    assert "locked.tmp" in hint, f"The hint must name the failed path; got: {hint!r}"
    assert "failed while applying" in hint, (
        f"The hint must name the apply-time failure; got: {hint!r}"
    )


def test_failed_gitignore_beside_applied_gitignore_loops_back() -> None:
    """A failed gitignore pattern alongside a successful one must loop back."""
    report = CommitReviewApplyReport(
        applied_gitignore_patterns=["build/"],
        failed_gitignore_patterns=["*.log"],
    )

    hint = _loopback_hint(report)

    assert "*.log" in hint, f"The hint must name the failed pattern; got: {hint!r}"
    assert "add_to_gitignore" in hint, f"The hint must name the failing action kind; got: {hint!r}"


def test_failed_exclude_beside_applied_exclude_loops_back() -> None:
    """A failed exclude pattern alongside a successful one must loop back."""
    report = CommitReviewApplyReport(
        applied_exclude_patterns=[".venv/"],
        failed_exclude_patterns=[".env"],
    )

    hint = _loopback_hint(report)

    assert ".env" in hint, f"The hint must name the failed pattern; got: {hint!r}"
    assert "add_to_git_exclude" in hint, (
        f"The hint must name the failing action kind; got: {hint!r}"
    )


def test_failed_apply_without_any_applied_action_loops_back() -> None:
    """A batch whose every action failed must loop the agent back."""
    report = CommitReviewApplyReport(failed_delete_paths=["locked.tmp"])

    hint = _loopback_hint(report)

    assert "locked.tmp" in hint, f"The hint must name the failed path; got: {hint!r}"


def test_declined_delete_keeps_the_unsafe_delete_hint() -> None:
    """A classifier-rejected delete keeps the structured unsafe-delete hint.

    The rejection wording ("Rejected paths") belongs to the safety
    classifier, so an unsafe delete must keep carrying it.
    """
    report = CommitReviewApplyReport(declined_delete_paths=["module.py"])

    hint = _loopback_hint(report)

    assert "module.py" in hint, f"The hint must name the rejected path; got: {hint!r}"
    assert "Rejected paths" in hint, (
        f"A declined delete must keep the unsafe-delete hint; got: {hint!r}"
    )


def test_declined_and_failed_actions_name_both_in_the_hint() -> None:
    """A report with a rejected delete and a failed apply names both."""
    report = CommitReviewApplyReport(
        declined_delete_paths=["module.py"],
        failed_exclude_patterns=[".env"],
    )

    hint = _loopback_hint(report)

    assert "module.py" in hint, f"The hint must name the rejected path; got: {hint!r}"
    assert ".env" in hint, f"The hint must name the failed pattern; got: {hint!r}"


def test_applied_actions_only_approve() -> None:
    """A batch whose every action applied must let the commit phase run."""
    report = CommitReviewApplyReport(
        applied_delete_paths=["a.tmp"],
        applied_gitignore_patterns=["build/"],
    )

    outcome = decide_commit_review_outcome(_PHASE_NAME, report)

    assert outcome is None, (
        f"Applied actions with no failure and no decline must approve; got: {outcome!r}"
    )


def test_empty_report_approves() -> None:
    """An empty report carries no failure and no decline, so it approves."""
    outcome = decide_commit_review_outcome(_PHASE_NAME, CommitReviewApplyReport())

    assert outcome is None, f"An empty report must approve; got: {outcome!r}"
