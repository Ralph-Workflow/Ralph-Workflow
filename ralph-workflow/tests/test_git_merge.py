"""Behavioral tests for :mod:`ralph.git.merge` (merge + fast-forward primitives).

These tests drive real ``git`` subprocesses against per-test
repositories built from the ``tmp_git_repo`` fixture, with real
``git worktree add`` to exercise the worktree-aware fast-forward
path and the ``MERGE_HEAD`` conflict path. They are excluded from
the budget-tracked 60s ``make verify`` step via
``pytest.mark.subprocess_e2e`` and run under ``make
test-subprocess-e2e`` with a per-suite 60s cap (matching the
convention in :mod:`tests.test_git_rebase`).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from git import Repo

from ralph.git.merge import (
    abort_merge,
    branch_exists,
    branch_sha,
    compare_and_swap_branch,
    conflict_stage_entries,
    fast_forward_via_worktree,
    is_ancestor,
    merge_in_progress,
    merge_target_into_current,
    observe_branch_sha,
    paths_with_conflict_markers,
    reset_hard,
    resolve_origin_head_branch,
    worktree_for_branch,
)

# File-level markers: ``subprocess_e2e`` excludes this file from
# ``make test`` (the budget-tracked 60s step) and ``timeout_seconds(5)``
# sizes the budget for a real process spawn. Matches the convention in
# ``tests/test_git_rebase.py``.
pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in ``repo_root`` with a 10s timeout."""
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def _base_branch(tmp_git_repo: Path) -> str:
    """Return the seed template's default branch name (master or main).

    The conftest's session-scoped template uses bare ``Repo.init`` with
    no ``-b main`` flag, so the default branch name depends on the
    test host's git version. Tests stay agnostic by reading
    ``HEAD`` symbolically.
    """
    out = _run(tmp_git_repo, "symbolic-ref", "--quiet", "HEAD")
    return out.stdout.strip().removeprefix("refs/heads/")


def _add_worktree(repo_root: Path, wt_path: Path, branch: str) -> subprocess.CompletedProcess[str]:
    """Create ``wt_path`` checking out ``branch`` in a sibling worktree."""
    return _run(repo_root, "worktree", "add", str(wt_path), branch)


def _commit_file(repo_root: Path, filename: str, content: str, message: str) -> str:
    """Write ``filename`` with ``content``, stage it, and commit on the current branch."""
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run(repo_root, "add", filename)
    _run(repo_root, "commit", "-m", message)
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def test_branch_exists_true_and_false(tmp_git_repo: Path) -> None:
    """``branch_exists`` is True for the seed branch and False for a missing one."""
    base = _base_branch(tmp_git_repo)
    assert branch_exists(tmp_git_repo, base) is True
    assert branch_exists(tmp_git_repo, "definitely-not-a-branch-xyz") is False


def test_branch_sha_returns_sha_or_none(tmp_git_repo: Path) -> None:
    """``branch_sha`` returns the SHA for an existing branch and ``None`` otherwise."""
    base = _base_branch(tmp_git_repo)
    expected = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert branch_sha(tmp_git_repo, base) == expected
    assert branch_sha(tmp_git_repo, "missing-branch") is None


def test_observe_branch_sha_separates_an_absent_branch_from_a_failed_query(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    """A failed ``git rev-parse`` is reported distinguishably from 'absent'.

    Defect this pins: ``branch_sha`` returned ``None`` on ANY non-zero
    return code, collapsing 'the branch does not exist' (exit 1) and
    'the invocation failed' (exit 128 -- not a repository, a contended
    ref lock, a broken object store) into one verdict. Downstream, the
    fast-forward mapped that single ``None`` onto a NON-retryable
    'target branch missing', so a ref lock held by a sibling agent
    landing on the same branch was abandoned instead of retried.

    ``tmp_path`` is a directory that is not a git repository, which
    produces the failed-invocation exit code without having to contend a
    real lock.
    """
    base = _base_branch(tmp_git_repo)
    expected = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()

    assert observe_branch_sha(tmp_git_repo, base) == (expected, True)
    # Definitely answered, and the answer is "no such branch".
    assert observe_branch_sha(tmp_git_repo, "missing-branch") == (None, True)
    # Not answered at all: the ref's state is UNKNOWN.
    assert observe_branch_sha(tmp_path, base) == (None, False)

    # ``branch_sha``'s own contract is unchanged for every existing
    # caller: both non-SHA cases still read as ``None``.
    assert branch_sha(tmp_git_repo, "missing-branch") is None
    assert branch_sha(tmp_path, base) is None


def test_is_ancestor_true_and_false(tmp_git_repo: Path) -> None:
    """``is_ancestor`` is True for reachable ancestors, False otherwise."""
    base = _base_branch(tmp_git_repo)
    base_sha = branch_sha(tmp_git_repo, base)
    assert base_sha is not None
    # Commit on a SEPARATE feature branch so the base ref doesn't move.
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit_file(tmp_git_repo, "feat.txt", "feature\n", "add feat")
    head_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    # base_sha is reachable from HEAD (the new commit is on top of base_sha).
    assert is_ancestor(tmp_git_repo, base_sha, head_sha) is True
    # HEAD is NOT reachable from base_sha (HEAD is ahead of base on a
    # separate branch).
    assert is_ancestor(tmp_git_repo, head_sha, base_sha) is False


def test_merge_target_into_current_clean_returns_success(tmp_git_repo: Path) -> None:
    """A clean three-way merge against the current branch returns ``success``."""
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit_file(tmp_git_repo, "feature.txt", "feature body\n", "feature work")
    feature_tip = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "checkout", base)
    _commit_file(tmp_git_repo, "mainline.txt", "mainline body\n", "mainline work")
    _run(tmp_git_repo, "checkout", "feature")
    result = merge_target_into_current(tmp_git_repo, base)
    assert result.outcome == "success"
    assert merge_in_progress(tmp_git_repo) is False
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""
    assert (tmp_git_repo / "feature.txt").exists()
    assert (tmp_git_repo / "mainline.txt").exists()
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() != feature_tip


def test_merge_target_into_current_conflict_aborts_cleanly(tmp_git_repo: Path) -> None:
    """A conflicting merge returns ``conflict`` and leaves a clean working tree."""
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit_file(tmp_git_repo, "shared.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit_file(tmp_git_repo, "shared.txt", "mainline version\n", "mainline shared")
    _run(tmp_git_repo, "checkout", "feature")
    feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    result = merge_target_into_current(tmp_git_repo, base)
    assert result.outcome == "conflict"
    assert merge_in_progress(tmp_git_repo) is False
    assert _run(tmp_git_repo, "diff", "--name-only", "--diff-filter=U").stdout.strip() == ""
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == feature_sha


def test_reset_hard_restores_branch_to_pre_integration_sha(tmp_git_repo: Path) -> None:
    """``reset_hard`` moves HEAD back to the supplied SHA and clears the tree."""
    original_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit_file(tmp_git_repo, "temp.txt", "temporary\n", "temp commit")
    new_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    assert new_sha != original_sha
    reset_hard(tmp_git_repo, original_sha)
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == original_sha
    assert not (tmp_git_repo / "temp.txt").exists()
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""


def test_fast_forward_via_worktree_advances_checked_out_branch(
    tmp_git_repo: Path,
) -> None:
    """``fast_forward_via_worktree`` returns True and advances the worktree branch."""
    base = _base_branch(tmp_git_repo)
    # Build a feature branch so we can move base without losing the
    # working-tree state we want.
    _run(tmp_git_repo, "checkout", "-b", "feature")
    # Move base forward via the linked worktree pattern: create a new
    # branch that mirrors base, worktree on it, and commit there.
    wt_branch = "wt-base-tmp"
    _run(tmp_git_repo, "branch", wt_branch, base)
    wt_path = tmp_git_repo.parent / f"{tmp_git_repo.name}-wt"
    _add_worktree(tmp_git_repo, wt_path, wt_branch)
    try:
        # Commit on the worktree's branch to advance it.
        target = wt_path / "main_only.txt"
        target.write_text("mainline\n", encoding="utf-8")
        _run(wt_path, "add", "main_only.txt")
        _run(wt_path, "commit", "-m", "main only")
        new_tip = _run(wt_path, "rev-parse", "HEAD").stdout.strip()
        # Now switch back to the primary repo and try to fast-forward
        # the wt branch to the new tip. The primary repo is on
        # ``feature`` (different branch) so the worktree's HEAD is the
        # only checkout of wt_branch.
        ok = fast_forward_via_worktree(wt_path, new_tip)
        assert ok is True
        wt_branch_sha = _run(wt_path, "rev-parse", f"refs/heads/{wt_branch}").stdout.strip()
        assert wt_branch_sha == new_tip
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(wt_path))


def test_fast_forward_via_worktree_refuses_non_fast_forward(
    tmp_git_repo: Path,
) -> None:
    """``fast_forward_via_worktree`` returns False (ref untouched) on non-ff."""
    base = _base_branch(tmp_git_repo)
    # Build a feature branch with a divergent tip. ``checkout -b
    # feature`` is safe here because the primary repo is currently
    # on ``base`` -- git's only restriction is that the SAME branch
    # can't be checked out in two worktrees simultaneously.
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit_file(tmp_git_repo, "feature_div.txt", "divergent\n", "divergent")
    divergent_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    # Move base forward so base is NOT an ancestor of divergent_sha.
    _run(tmp_git_repo, "checkout", base)
    _commit_file(tmp_git_repo, "base_forward.txt", "base forward\n", "base forward")
    # Add a worktree checking out a NEW branch (which mirrors
    # ``base`` after the forward commit) so the worktree's HEAD is
    # at base's tip. ``fast_forward_via_worktree`` advances the
    # branch currently checked out in the worktree to the supplied
    # SHA. We use a fresh branch name because ``base`` is already
    # checked out in the primary repo.
    wt_branch = "wt-base-tmp"
    _run(tmp_git_repo, "branch", wt_branch, base)
    wt_path = tmp_git_repo.parent / f"{tmp_git_repo.name}-wt"
    _add_worktree(tmp_git_repo, wt_path, wt_branch)
    try:
        before = _run(wt_path, "rev-parse", f"refs/heads/{wt_branch}").stdout.strip()
        ok = fast_forward_via_worktree(wt_path, divergent_sha)
        assert ok is False
        after = _run(wt_path, "rev-parse", f"refs/heads/{wt_branch}").stdout.strip()
        assert after == before
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(wt_path))


def test_compare_and_swap_branch_succeeds_when_old_sha_matches(
    tmp_git_repo: Path,
) -> None:
    """``compare_and_swap_branch`` updates the ref when the observed SHA matches."""
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    new_sha = _commit_file(tmp_git_repo, "bumped.txt", "bump\n", "bump")
    observed = branch_sha(tmp_git_repo, base)
    assert observed is not None and observed != new_sha
    assert _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip() == observed
    ok = compare_and_swap_branch(tmp_git_repo, base, observed, new_sha)
    assert ok is True
    assert _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip() == new_sha


def test_compare_and_swap_branch_fails_closed_on_stale_old_sha(
    tmp_git_repo: Path,
) -> None:
    """``compare_and_swap_branch`` returns False and leaves the ref untouched when stale."""
    base = _base_branch(tmp_git_repo)
    observed = branch_sha(tmp_git_repo, base)
    assert observed is not None
    # Simulate a concurrent landing on base: create a feature branch
    # so committing does not move base, then explicitly move base
    # forward via the worktree. Using a feature branch avoids any
    # accidental base ref advancement from our commit.
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit_file(tmp_git_repo, "concurrent.txt", "concurrent\n", "concurrent")
    concurrent_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    # Force base to point at the new commit (simulating a concurrent
    # landing that we observed AFTER we captured ``observed``).
    _run(tmp_git_repo, "branch", "-f", base, concurrent_sha)
    post_concurrent = branch_sha(tmp_git_repo, base)
    assert post_concurrent is not None and post_concurrent != observed
    # Now attempt CAS with the STALE observed SHA -> must fail closed.
    stale_target_sha = _run(tmp_git_repo, "rev-parse", "HEAD~1").stdout.strip()
    ok = compare_and_swap_branch(tmp_git_repo, base, observed, stale_target_sha)
    assert ok is False
    assert branch_sha(tmp_git_repo, base) == post_concurrent


def test_worktree_for_branch_returns_correct_path_and_none(
    tmp_git_repo: Path,
) -> None:
    """``worktree_for_branch`` returns the linked worktree's path or ``None``."""
    _base_branch(tmp_git_repo)  # ensure the seed base branch exists
    wt_branch = "wt-tmp-branch"
    wt_path = tmp_git_repo.parent / f"{tmp_git_repo.name}-wt"
    # Create the branch from the current commit WITHOUT switching the
    # primary repo onto it (so the worktree-add below doesn't fail
    # with ``'wt-tmp-branch' is already used by worktree``).
    _run(tmp_git_repo, "branch", wt_branch)
    _add_worktree(tmp_git_repo, wt_path, wt_branch)
    try:
        found = worktree_for_branch(tmp_git_repo, wt_branch)
        assert found is not None
        assert Path(found).resolve() == wt_path.resolve()
        assert worktree_for_branch(tmp_git_repo, "definitely-not-checked-out") is None
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(wt_path))


def test_resolve_origin_head_branch_returns_default_branch(
    tmp_git_repo: Path,
) -> None:
    """``resolve_origin_head_branch`` returns the origin default branch when set."""
    base = _base_branch(tmp_git_repo)
    bare_root = tmp_git_repo.parent / "bare.git"
    _run(tmp_git_repo.parent, "init", "--bare", str(bare_root))
    _run(tmp_git_repo, "remote", "add", "origin", str(bare_root))
    # Push the local branch so the remote has refs to point HEAD at.
    _run(tmp_git_repo, "push", "origin", base)
    # Force origin/HEAD to a known branch (matches what
    # ``git clone`` would set up).
    _run(tmp_git_repo, "remote", "set-head", "origin", base)
    assert resolve_origin_head_branch(tmp_git_repo) == base


def test_resolve_origin_head_branch_returns_none_without_remote(
    tmp_git_repo: Path,
) -> None:
    """``resolve_origin_head_branch`` returns None when no origin remote is configured."""
    # The seed template repo has no remote by default.
    assert resolve_origin_head_branch(tmp_git_repo) is None


def test_merge_regression_lone_conflict_marker_is_reported(
    tmp_git_repo: Path,
) -> None:
    """A half-deleted conflict hunk is still an unresolved conflict.

    The scan used to require BOTH an opening ``<<<<<<< `` and a closing
    ``>>>>>>> `` line before reporting a path, so a resolver that
    deleted only one of the two fences produced an empty report.
    ``git add`` clears the unmerged bit, so that empty report was the
    only remaining gate and the marker-bearing file was committed as a
    resolution. Either fence alone now reports the path; a lone
    ``=======`` in ordinary prose deliberately still does not.
    """
    (tmp_git_repo / "open_only.txt").write_text(
        "<<<<<<< HEAD\nfeature version\nbase version\n", encoding="utf-8"
    )
    (tmp_git_repo / "close_only.txt").write_text(
        "feature version\nbase version\n>>>>>>> main\n", encoding="utf-8"
    )
    (tmp_git_repo / "prose.txt").write_text(
        "Heading\n=======\nordinary reStructuredText prose\n", encoding="utf-8"
    )

    reported = paths_with_conflict_markers(
        tmp_git_repo, ["open_only.txt", "close_only.txt", "prose.txt"]
    )

    assert reported == ["open_only.txt", "close_only.txt"]


def test_paths_with_conflict_markers_ignores_clean_and_unreadable_paths(
    tmp_git_repo: Path,
) -> None:
    """The positive control: a resolved file and a missing file stay silent."""
    (tmp_git_repo / "resolved.txt").write_text("merged content\n", encoding="utf-8")

    assert (
        paths_with_conflict_markers(
            tmp_git_repo, ["resolved.txt", "never_written.txt"]
        )
        == []
    )


def test_abort_merge_is_safe_when_no_merge_in_progress(tmp_git_repo: Path) -> None:
    """``abort_merge`` is a no-op when no merge is in progress."""
    assert merge_in_progress(tmp_git_repo) is False
    abort_merge(tmp_git_repo)
    assert merge_in_progress(tmp_git_repo) is False
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""


__all__ = ["Repo"]


def test_conflict_stage_entries_parses_mode_only_and_gitlink(
    monkeypatch: pytest.MonkeyPatch, tmp_git_repo: Path
) -> None:
    """NUL-delimited stage records preserve modes, gitlinks, and spaced paths."""
    from ralph.git import merge as merge_module

    output = (
        "100644 base 1\tspace path\0"
        "100644 blob 2\tspace path\0"
        "100755 blob 3\tspace path\0"
        "160000 old 2\tsub\0"
        "160000 new 3\tsub\0"
    )
    monkeypatch.setattr(
        merge_module,
        "run_git",
        lambda *_args, **_kwargs: type("Result", (), {"returncode": 0, "stdout": output})(),
    )

    assert conflict_stage_entries(tmp_git_repo, ["space path", "sub"]) == {
        "space path": {1: ("100644", "base"), 2: ("100644", "blob"), 3: ("100755", "blob")},
        "sub": {2: ("160000", "old"), 3: ("160000", "new")},
    }


def test_conflict_stage_entries_returns_empty_when_git_query_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_git_repo: Path
) -> None:
    """A failed index query declines deterministic resolution safely."""
    from ralph.git import merge as merge_module

    monkeypatch.setattr(
        merge_module,
        "run_git",
        lambda *_args, **_kwargs: type("Result", (), {"returncode": 1, "stdout": ""})(),
    )

    assert conflict_stage_entries(tmp_git_repo, ["conflict"]) == {}
