"""Table tests for exact worktree HEAD-ref matching.

The substring check these tests replace made a sibling linked worktree
whose branch name merely has this branch as a strict prefix look like a
genuine "branch already checked out elsewhere" conflict, which silently
disabled auto-integration for a whole run.
"""

from __future__ import annotations

from ralph.git.rebase._worktree_head_ref import head_file_targets_branch


def test_exact_branch_ref_matches() -> None:
    assert head_file_targets_branch("ref: refs/heads/wt-040\n", "wt-040") is True


def test_prefix_colliding_sibling_branch_does_not_match() -> None:
    # Regression: the old ``target_ref in content`` substring test saw
    # 'refs/heads/wt-040' inside 'refs/heads/wt-040-fix-autorebase' and
    # raised RebasePreconditionError for an unrelated sibling worktree.
    assert head_file_targets_branch("ref: refs/heads/wt-040-fix-autorebase\n", "wt-040") is False


def test_longer_branch_against_shorter_head_does_not_match() -> None:
    assert head_file_targets_branch("ref: refs/heads/wt-040\n", "wt-040-fix-autorebase") is False


def test_detached_head_sha_does_not_match() -> None:
    assert head_file_targets_branch("4f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f90\n", "wt-040") is False


def test_empty_or_blank_content_does_not_match() -> None:
    assert head_file_targets_branch("", "wt-040") is False
    assert head_file_targets_branch("   \n", "wt-040") is False


def test_ref_without_the_ref_prefix_matches() -> None:
    assert head_file_targets_branch("refs/heads/wt-040\n", "wt-040") is True


def test_leading_and_trailing_whitespace_is_tolerated() -> None:
    assert head_file_targets_branch("  ref: refs/heads/wt-040  \n", "wt-040") is True
