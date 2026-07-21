"""Fail-closed contracts for the git-state queries behind auto-integration.

Three queries used to answer "nothing there" and "I could not look"
with the SAME value, which is what let auto-integration corrupt or
silently skip work:

* ``worktree_for_branch`` returned ``None`` both when the branch was
  checked out nowhere and when ``git worktree list`` failed, so the
  fast-forward compare-and-swapped the shared mainline ref while a live
  checkout may have held it.
* ``merge_in_progress`` returned ``False`` when ``git rev-parse
  --git-dir`` failed, making ``abort_merge`` a silent no-op that could
  strand ``MERGE_HEAD`` and block every later integration.
* ``abort_merge`` never reported whether the abort actually ran.

Every test here injects git through ``ralph.git.merge.run_git`` and
touches no repository, so the file stays in the DEFAULT (budget-tracked)
suite: ``worktree_lookup``, ``merge_state`` and ``abort_merge`` pass
``repo_root`` to ``run_git`` as ``cwd`` and never open a GitPython
``Repo``.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.git_run_result import GitRunResult
from ralph.git.merge import (
    MERGE_STATE_IN_PROGRESS,
    MERGE_STATE_NONE,
    MERGE_STATE_UNKNOWN,
    WORKTREE_FOUND,
    WORKTREE_NOT_CHECKED_OUT,
    WORKTREE_QUERY_FAILED,
    abort_merge,
    merge_in_progress,
    merge_state,
    worktree_for_branch,
    worktree_lookup,
)
from ralph.pipeline.auto_integrate_ff import (
    fast_forward_target,
    is_retryable_fast_forward_failure,
)

if TYPE_CHECKING:
    import pytest

    from ralph.git.subprocess_runner import GitRunOptions

_WORKTREE_LIST_OUTPUT = (
    "worktree /repos/main\n"
    "HEAD 1111111111111111111111111111111111111111\n"
    "branch refs/heads/main\n"
    "\n"
    "worktree /repos/feature\n"
    "HEAD 2222222222222222222222222222222222222222\n"
    "branch refs/heads/feature\n"
)


def _result(args: tuple[str, ...], returncode: int, stdout: str = "") -> GitRunResult:
    """Build a :class:`GitRunResult` for an injected git invocation."""
    return GitRunResult(args=args, returncode=returncode, stdout=stdout, stderr="")


def _install_git_stub(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[tuple[str, ...], tuple[int, str]],
) -> list[tuple[str, ...]]:
    """Route ``ralph.git.merge.run_git`` to canned results; record every argv."""
    calls: list[tuple[str, ...]] = []

    def _fake_run_git(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        label: str = "",
        options: GitRunOptions | None = None,
    ) -> GitRunResult:
        argv = tuple(args)
        calls.append(argv)
        returncode, stdout = responses.get(argv, (0, ""))
        return _result(argv, returncode, stdout)

    monkeypatch.setattr("ralph.git.merge.run_git", _fake_run_git)
    return calls


def test_merge_regression_unreadable_git_dir_is_not_reported_as_no_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed ``rev-parse --git-dir`` must not answer 'no merge in progress'."""
    _install_git_stub(
        monkeypatch, {("rev-parse", "--git-dir"): (128, "")}
    )

    assert merge_state(Path("/repos/main")) == MERGE_STATE_UNKNOWN
    assert merge_state(Path("/repos/main")) != MERGE_STATE_NONE


def test_merge_regression_unreadable_git_dir_abort_reports_it_did_not_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``abort_merge`` on an unreadable repository reports failure, not silence."""
    calls = _install_git_stub(
        monkeypatch,
        {
            ("rev-parse", "--git-dir"): (128, ""),
            ("merge", "--abort"): (128, ""),
        },
    )

    aborted = abort_merge(Path("/repos/main"))

    assert aborted is False
    # Fail closed: an unreadable repository is NOT assumed to be clean, so
    # the abort is attempted rather than skipped.
    assert ("merge", "--abort") in calls


def test_clean_repo_without_merge_head_still_means_no_merge_in_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The positive case is unchanged: a readable repo with no MERGE_HEAD."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    _install_git_stub(
        monkeypatch, {("rev-parse", "--git-dir"): (0, str(git_dir))}
    )

    assert merge_state(tmp_path) == MERGE_STATE_NONE
    assert merge_in_progress(tmp_path) is False


def test_merge_head_present_means_merge_in_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other positive case: MERGE_HEAD present is reported as in progress."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "MERGE_HEAD").write_text("cafebabe\n", encoding="utf-8")
    _install_git_stub(
        monkeypatch, {("rev-parse", "--git-dir"): (0, str(git_dir))}
    )

    assert merge_state(tmp_path) == MERGE_STATE_IN_PROGRESS
    assert merge_in_progress(tmp_path) is True


def test_merge_regression_worktree_query_failure_is_not_branch_not_checked_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed ``worktree list`` is distinct from 'checked out nowhere'."""
    _install_git_stub(
        monkeypatch, {("worktree", "list", "--porcelain"): (128, "")}
    )

    status, path = worktree_lookup(Path("/repos/main"), "main")

    assert status == WORKTREE_QUERY_FAILED
    assert status != WORKTREE_NOT_CHECKED_OUT
    assert path is None


def test_successful_worktree_query_without_match_means_not_checked_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The paired positive case: a clean query with no matching branch."""
    _install_git_stub(
        monkeypatch,
        {("worktree", "list", "--porcelain"): (0, _WORKTREE_LIST_OUTPUT)},
    )

    status, path = worktree_lookup(Path("/repos/main"), "no-such-branch")

    assert status == WORKTREE_NOT_CHECKED_OUT
    assert path is None


def test_worktree_lookup_finds_the_checkout_holding_the_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The found case still returns the holding worktree path."""
    _install_git_stub(
        monkeypatch,
        {("worktree", "list", "--porcelain"): (0, _WORKTREE_LIST_OUTPUT)},
    )

    status, path = worktree_lookup(Path("/repos/main"), "feature")

    assert status == WORKTREE_FOUND
    assert path == Path("/repos/feature")


def test_worktree_for_branch_keeps_its_optional_returning_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The legacy wrapper is unchanged so existing callers keep compiling."""
    _install_git_stub(
        monkeypatch,
        {("worktree", "list", "--porcelain"): (0, _WORKTREE_LIST_OUTPUT)},
    )

    assert worktree_for_branch(Path("/repos/main"), "feature") == Path(
        "/repos/feature"
    )
    assert worktree_for_branch(Path("/repos/main"), "no-such-branch") is None


_TARGET_SHA = "1111111111111111111111111111111111111111"
_FEATURE_SHA = "3333333333333333333333333333333333333333"


def _install_fast_forward_stub(
    monkeypatch: pytest.MonkeyPatch, worktree_list: tuple[int, str]
) -> list[tuple[str, ...]]:
    """Drive ``fast_forward_target`` past its ancestry checks to the branch table."""
    calls = _install_git_stub(
        monkeypatch,
        {
            ("rev-parse", "--verify", "--quiet", "refs/heads/main"): (
                0,
                _TARGET_SHA,
            ),
            ("merge-base", "--is-ancestor", _TARGET_SHA, _FEATURE_SHA): (0, ""),
            ("worktree", "list", "--porcelain"): worktree_list,
        },
    )
    # find_main_worktree_root opens a real GitPython Repo; the fast-forward
    # branch table under test does not depend on which root it returns.
    def _identity_root(repo_root: Path) -> Path:
        return repo_root

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_ff.find_main_worktree_root", _identity_root
    )
    return calls


def test_fast_forward_regression_worktree_query_failure_is_a_retryable_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed worktree query must not compare-and-swap the shared ref."""
    calls = _install_fast_forward_stub(monkeypatch, (128, ""))

    landed, reason = fast_forward_target(Path("/repos/feature"), "main", _FEATURE_SHA)

    assert landed is False
    assert reason
    assert is_retryable_fast_forward_failure(reason) is True
    # The shared mainline ref was NOT moved while a live checkout may hold it.
    assert not any(argv and argv[0] == "update-ref" for argv in calls)


def test_fast_forward_still_uses_the_cas_when_nothing_holds_the_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The paired positive case: a clean query with no match takes the CAS path."""
    calls = _install_fast_forward_stub(
        monkeypatch, (0, _WORKTREE_LIST_OUTPUT.replace("refs/heads/main", "refs/heads/other"))
    )

    landed, reason = fast_forward_target(Path("/repos/feature"), "main", _FEATURE_SHA)

    assert landed is True
    assert reason == ""
    assert (
        "update-ref",
        "refs/heads/main",
        _FEATURE_SHA,
        _TARGET_SHA,
    ) in calls
