from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.git_run_result import GitRunResult
from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.git.merge import (
    MERGE_STATE_IN_PROGRESS,
    MERGE_STATE_NONE,
    MERGE_STATE_UNKNOWN,
    WORKTREE_FOUND,
    WORKTREE_NOT_CHECKED_OUT,
    WORKTREE_QUERY_FAILED,
    MergeResult,
    abort_merge,
    commit_merge_in_progress,
    merge_in_progress,
    merge_state,
    merge_target_into_current,
    worktree_for_branch,
    worktree_lookup,
)
from ralph.pipeline.auto_integrate_ff import (
    fast_forward_target,
    is_retryable_fast_forward_failure,
)
from ralph.pipeline.auto_integrate_resolve import endpoint_merge_with_resolution
from ralph.pipeline.resolution_outcome import ResolvedOutcome

if TYPE_CHECKING:
    import pytest

    from ralph.git.git_call_policy import GitCallClass
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
    return GitRunResult(args=args, returncode=returncode, stdout=stdout, stderr="")


def _install_git_stub(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[tuple[str, ...], tuple[int, str]],
) -> tuple[list[tuple[str, ...]], list[tuple[str, GitCallClass | None]]]:
    calls: list[tuple[str, ...]] = []
    call_records: list[tuple[str, GitCallClass | None]] = []

    def _fake_run_git(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        label: str = "",
        options: GitRunOptions | None = None,
        call_class: GitCallClass | None = None,
    ) -> GitRunResult:
        raw_argv = tuple(args)
        argv = (
            raw_argv[len(COMMIT_PIN_CONFIG_ARGS) :]
            if raw_argv[: len(COMMIT_PIN_CONFIG_ARGS)] == COMMIT_PIN_CONFIG_ARGS
            else raw_argv
        )
        calls.append(argv)
        call_records.append((label, call_class))
        returncode, stdout = responses.get(argv, (0, ""))
        return _result(raw_argv, returncode, stdout)

    monkeypatch.setattr("ralph.git.merge.run_git", _fake_run_git)
    monkeypatch.setattr("ralph.git.merge_obstructions.run_git", _fake_run_git)
    return calls, call_records


def test_merge_regression_unreadable_git_dir_is_not_reported_as_no_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_git_stub(monkeypatch, {("rev-parse", "--git-dir"): (128, "")})

    assert merge_state(Path("/repos/main")) == MERGE_STATE_UNKNOWN
    assert merge_state(Path("/repos/main")) != MERGE_STATE_NONE


def test_merge_regression_unreadable_git_dir_abort_reports_it_did_not_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _ = _install_git_stub(
        monkeypatch,
        {
            ("rev-parse", "--git-dir"): (128, ""),
            ("merge", "--abort"): (128, ""),
        },
    )

    aborted = abort_merge(Path("/repos/main"))

    assert aborted is False
    assert ("merge", "--abort") in calls


def test_clean_repo_without_merge_head_still_means_no_merge_in_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    _install_git_stub(monkeypatch, {("rev-parse", "--git-dir"): (0, str(git_dir))})

    assert merge_state(tmp_path) == MERGE_STATE_NONE
    assert merge_in_progress(tmp_path) is False


def test_merge_head_present_means_merge_in_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "MERGE_HEAD").write_text("cafebabe\n", encoding="utf-8")
    _install_git_stub(monkeypatch, {("rev-parse", "--git-dir"): (0, str(git_dir))})

    assert merge_state(tmp_path) == MERGE_STATE_IN_PROGRESS
    assert merge_in_progress(tmp_path) is True


def test_merge_regression_worktree_query_failure_is_not_branch_not_checked_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_git_stub(monkeypatch, {("worktree", "list", "--porcelain"): (128, "")})

    status, path = worktree_lookup(Path("/repos/main"), "main")

    assert status == WORKTREE_QUERY_FAILED
    assert status != WORKTREE_NOT_CHECKED_OUT
    assert path is None


def test_successful_worktree_query_without_match_means_not_checked_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    _install_git_stub(
        monkeypatch,
        {("worktree", "list", "--porcelain"): (0, _WORKTREE_LIST_OUTPUT)},
    )

    assert worktree_for_branch(Path("/repos/main"), "feature") == Path("/repos/feature")
    assert worktree_for_branch(Path("/repos/main"), "no-such-branch") is None


_TARGET_SHA = "1111111111111111111111111111111111111111"
_FEATURE_SHA = "3333333333333333333333333333333333333333"


def _install_fast_forward_stub(
    monkeypatch: pytest.MonkeyPatch, worktree_list: tuple[int, str]
) -> tuple[list[tuple[str, ...]], list[tuple[str, GitCallClass | None]]]:
    calls, call_records = _install_git_stub(
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

    def _identity_root(repo_root: Path) -> Path:
        return repo_root

    monkeypatch.setattr("ralph.pipeline.auto_integrate_ff.find_main_worktree_root", _identity_root)
    return calls, call_records


def test_fast_forward_regression_worktree_query_failure_is_a_retryable_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _ = _install_fast_forward_stub(monkeypatch, (128, ""))

    outcome = fast_forward_target(Path("/repos/feature"), "main", _FEATURE_SHA)

    assert not isinstance(outcome, ResolvedOutcome)
    assert is_retryable_fast_forward_failure(outcome) is True
    assert not any(argv and argv[0] == "update-ref" for argv in calls)


def test_fast_forward_still_uses_the_cas_when_nothing_holds_the_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, call_records = _install_fast_forward_stub(
        monkeypatch, (0, _WORKTREE_LIST_OUTPUT.replace("refs/heads/main", "refs/heads/other"))
    )

    outcome = fast_forward_target(Path("/repos/feature"), "main", _FEATURE_SHA)

    assert isinstance(outcome, ResolvedOutcome)
    assert (
        "update-ref",
        "refs/heads/main",
        _FEATURE_SHA,
        _TARGET_SHA,
    ) in calls
    assert [call_class for label, call_class in call_records if label == "git-update-ref-cas"] == [
        "mutation"
    ]


def test_merge_regression_unreadable_git_dir_never_commits_a_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _ = _install_git_stub(
        monkeypatch,
        {
            ("rev-parse", "--git-dir"): (128, ""),
            ("commit", "--no-edit"): (0, ""),
        },
    )

    assert commit_merge_in_progress(Path("/repos/main")) is False
    assert ("commit", "--no-edit") not in calls


def _install_resolve_stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    state: str,
) -> list[str]:
    log: list[str] = []

    def _fake_merge(
        repo_root: Path | str, target: str, *, keep_conflicts: bool = False
    ) -> MergeResult:
        log.append("merge")
        return MergeResult(outcome="conflict")

    def _fake_merge_state(repo_root: Path | str) -> str:
        return state

    def _fake_abort(repo_root: Path | str) -> bool:
        log.append("abort")
        return False

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_resolve.merge_target_into_current",
        _fake_merge,
    )
    monkeypatch.setattr("ralph.pipeline.auto_integrate_resolve.merge_state", _fake_merge_state)
    monkeypatch.setattr("ralph.pipeline.auto_integrate_resolve.abort_merge", _fake_abort)
    return log


def test_resolution_regression_unreadable_merge_state_retains_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = _install_resolve_stub(monkeypatch, state=MERGE_STATE_UNKNOWN)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_resolve.mark_pending_merge", lambda *_args, **_kwargs: None
    )

    def _resolver(repo_root: Path, target: str) -> bool:
        log.append("resolver")
        return True

    outcome = endpoint_merge_with_resolution(Path("/repos/main"), "main", _resolver)

    assert outcome is not None
    assert outcome.outcome == "merge_commit_pending"
    assert "abort" not in log, "an unreadable merge state must preserve possible progress"
    assert "resolver" not in log, "there are no readable conflicted paths to hand a resolver"


def test_resolution_readable_no_merge_state_returns_the_plain_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = _install_resolve_stub(monkeypatch, state=MERGE_STATE_NONE)

    def _resolver(repo_root: Path, target: str) -> bool:
        log.append("resolver")
        return True

    outcome = endpoint_merge_with_resolution(Path("/repos/main"), "main", _resolver)

    assert outcome is not None
    assert outcome.outcome == "conflict"
    assert log == ["merge"], (
        f"git positively reported no MERGE_HEAD: nothing to abort, nothing to resolve; got {log}"
    )


def _install_sequenced_git_stub(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[tuple[str, ...], list[tuple[int, str]]],
) -> tuple[list[tuple[str, ...]], list[tuple[str, GitCallClass | None]]]:
    calls: list[tuple[str, ...]] = []
    call_records: list[tuple[str, GitCallClass | None]] = []
    queues = {argv: list(results) for argv, results in responses.items()}

    def _fake_run_git(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        label: str = "",
        options: GitRunOptions | None = None,
        call_class: GitCallClass | None = None,
    ) -> GitRunResult:
        raw_argv = tuple(args)
        argv = (
            raw_argv[len(COMMIT_PIN_CONFIG_ARGS) :]
            if raw_argv[: len(COMMIT_PIN_CONFIG_ARGS)] == COMMIT_PIN_CONFIG_ARGS
            else raw_argv
        )
        calls.append(argv)
        call_records.append((label, call_class))
        queue = queues.get(argv)
        if not queue:
            return _result(raw_argv, 0, "")
        returncode, stdout = queue.pop(0) if len(queue) > 1 else queue[0]
        return _result(raw_argv, returncode, stdout)

    monkeypatch.setattr("ralph.git.merge.run_git", _fake_run_git)
    monkeypatch.setattr("ralph.git.merge_obstructions.run_git", _fake_run_git)
    return calls, call_records


def test_merge_regression_unreadable_git_dir_after_commit_is_not_a_merge_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "MERGE_HEAD").write_text("cafebabe\n", encoding="utf-8")
    calls, _ = _install_sequenced_git_stub(
        monkeypatch,
        {
            ("rev-parse", "--git-dir"): [(0, str(git_dir)), (128, "")],
            ("commit", "--no-edit"): [(0, "")],
        },
    )

    assert commit_merge_in_progress(tmp_path) is False
    assert ("commit", "--no-edit") in calls


def _capture_warnings() -> tuple[list[str], int]:
    captured: list[str] = []
    sink_id = logger.add(
        lambda message: captured.append(str(message)),
        level="WARNING",
        format="{message}",
    )
    return captured, sink_id


def test_merge_regression_unprovable_cleanup_abort_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _ = _install_git_stub(
        monkeypatch,
        {
            ("merge", "--no-edit", "--", "main"): (1, ""),
            ("rev-parse", "--git-dir"): (128, ""),
            ("merge", "--abort"): (128, ""),
        },
    )
    captured, sink_id = _capture_warnings()
    try:
        outcome = merge_target_into_current(Path("/repos/feature"), "main")
    finally:
        logger.remove(sink_id)

    assert outcome.outcome == "conflict"
    assert ("merge", "--abort") in calls
    assert any("not proven aborted" in line for line in captured), (
        f"the blocked repository must be named in a warning; got {captured}"
    )


def test_merge_cleanup_abort_is_silent_when_git_proves_the_tree_is_clean(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    calls, _ = _install_git_stub(
        monkeypatch,
        {
            ("merge", "--no-edit", "--", "main"): (1, ""),
            ("rev-parse", "--git-dir"): (0, str(git_dir)),
        },
    )
    captured, sink_id = _capture_warnings()
    try:
        outcome = merge_target_into_current(tmp_path, "main")
    finally:
        logger.remove(sink_id)

    assert outcome.outcome == "conflict"
    assert ("merge", "--abort") not in calls
    assert captured == []
