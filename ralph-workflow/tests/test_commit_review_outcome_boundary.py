from __future__ import annotations

from ralph.phases.commit_review import (
    CommitReviewApplyReport,
    CommitReviewLoopback,
    decide_commit_review_outcome,
)

_PHASE_NAME = "development_commit_review"


def _loopback_hint(report: CommitReviewApplyReport) -> str:
    outcome = decide_commit_review_outcome(_PHASE_NAME, report)
    assert isinstance(outcome, CommitReviewLoopback), (
        f"Expected a loopback for report {report!r}, got {outcome!r}"
    )
    return outcome.hint


def test_failed_delete_beside_applied_delete_loops_back() -> None:
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
    report = CommitReviewApplyReport(
        applied_gitignore_patterns=["build/"],
        failed_gitignore_patterns=["*.log"],
    )

    hint = _loopback_hint(report)

    assert "*.log" in hint, f"The hint must name the failed pattern; got: {hint!r}"
    assert "add_to_gitignore" in hint, f"The hint must name the failing action kind; got: {hint!r}"


def test_failed_exclude_beside_applied_exclude_loops_back() -> None:
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
    report = CommitReviewApplyReport(failed_delete_paths=["locked.tmp"])

    hint = _loopback_hint(report)

    assert "locked.tmp" in hint, f"The hint must name the failed path; got: {hint!r}"


def test_declined_delete_keeps_the_unsafe_delete_hint() -> None:
    report = CommitReviewApplyReport(declined_delete_paths=["module.py"])

    hint = _loopback_hint(report)

    assert "module.py" in hint, f"The hint must name the rejected path; got: {hint!r}"
    assert "Rejected paths" in hint, (
        f"A declined delete must keep the unsafe-delete hint; got: {hint!r}"
    )


def test_declined_and_failed_actions_name_both_in_the_hint() -> None:
    report = CommitReviewApplyReport(
        declined_delete_paths=["module.py"],
        failed_exclude_patterns=[".env"],
    )

    hint = _loopback_hint(report)

    assert "module.py" in hint, f"The hint must name the rejected path; got: {hint!r}"
    assert ".env" in hint, f"The hint must name the failed pattern; got: {hint!r}"


def test_applied_actions_only_approve() -> None:
    report = CommitReviewApplyReport(
        applied_delete_paths=["a.tmp"],
        applied_gitignore_patterns=["build/"],
    )

    outcome = decide_commit_review_outcome(_PHASE_NAME, report)

    assert outcome is None, (
        f"Applied actions with no failure and no decline must approve; got: {outcome!r}"
    )


def test_empty_report_approves() -> None:
    outcome = decide_commit_review_outcome(_PHASE_NAME, CommitReviewApplyReport())

    assert outcome is None, f"An empty report must approve; got: {outcome!r}"
