from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from git import GitCommandError, Repo

from ralph.git.merge import branch_sha, observe_unmerged_paths
from ralph.git.operation_snapshot import (
    OperationSnapshot,
    OperationState,
    observe_operation_snapshot,
)
from ralph.git.operations import get_head_sha
from ralph.git.rebase.rebase_continuation import rebase_in_progress_at


def _assign_head_sha(snapshot: OperationSnapshot, value: str | None) -> None:
    snapshot.__setattr__("head_sha", value)


def _assign_operation_state(snapshot: OperationSnapshot, value: OperationState) -> None:
    snapshot.__setattr__("operation_state", value)


def _commit(repo: Repo, path: str, content: str, message: str) -> str:
    target = Path(repo.working_tree_dir or "") / path
    target.write_text(content, encoding="utf-8")
    repo.index.add([path])
    return repo.index.commit(message).hexsha


def _conflicting_branches(repo: Repo) -> None:
    repo.git.checkout("-b", "feature")
    _commit(repo, "README.md", "feature\n", "feature")
    repo.git.checkout("main")
    _commit(repo, "README.md", "main\n", "main")


def test_clean_snapshot_matches_direct_git_reads_and_is_immutable(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    try:
        first = observe_operation_snapshot(tmp_git_repo, target="main")
        first_head = first.head_sha
        _commit(repo, "later.txt", "later\n", "later")
        second = observe_operation_snapshot(tmp_git_repo, target="main")
    finally:
        repo.close()

    assert first.operation_state is OperationState.NONE
    assert first.unmerged_paths == observe_unmerged_paths(tmp_git_repo).paths == ()
    assert first_head is not None
    assert first.head_sha == first_head
    assert second.head_sha == get_head_sha(tmp_git_repo)
    assert second.target_tip == branch_sha(tmp_git_repo, "main")
    assert first.head_sha != second.head_sha
    with pytest.raises(FrozenInstanceError):
        _assign_head_sha(first, second.head_sha)


def test_rebase_conflict_snapshot_matches_direct_git_reads(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    try:
        _conflicting_branches(repo)
        repo.git.checkout("feature")
        with pytest.raises(GitCommandError):
            repo.git.rebase("main")
        snapshot = observe_operation_snapshot(tmp_git_repo, target="main")
    finally:
        repo.close()

    direct = observe_unmerged_paths(tmp_git_repo)
    assert rebase_in_progress_at(tmp_git_repo)
    assert snapshot.operation_state is OperationState.REBASE_PAUSED
    assert snapshot.unmerged_query_ok is direct.query_ok
    assert snapshot.unmerged_paths == direct.paths == ("README.md",)
    assert snapshot.head_sha == get_head_sha(tmp_git_repo)
    assert snapshot.target_tip == branch_sha(tmp_git_repo, "main")
    assert snapshot.rebase_head is not None


def test_merge_conflict_snapshot_matches_direct_git_reads(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    try:
        _conflicting_branches(repo)
        with pytest.raises(GitCommandError):
            repo.git.merge("feature")
        snapshot = observe_operation_snapshot(tmp_git_repo, target="feature")
    finally:
        repo.close()

    direct = observe_unmerged_paths(tmp_git_repo)
    assert snapshot.operation_state is OperationState.MERGE_PAUSED
    assert snapshot.unmerged_paths == direct.paths == ("README.md",)
    assert snapshot.head_sha == get_head_sha(tmp_git_repo)
    assert snapshot.target_tip == branch_sha(tmp_git_repo, "feature")
    assert snapshot.merge_head == branch_sha(tmp_git_repo, "feature")


def test_detached_head_snapshot_matches_direct_git_reads(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    try:
        repo.git.checkout("--detach", "HEAD")
        snapshot = observe_operation_snapshot(tmp_git_repo, target="main")
        direct_detached = repo.head.is_detached
    finally:
        repo.close()

    assert direct_detached
    assert snapshot.operation_state is OperationState.DETACHED
    assert snapshot.head_sha == get_head_sha(tmp_git_repo)
    assert snapshot.target_tip == branch_sha(tmp_git_repo, "main")


def test_stale_rebase_directory_without_matching_facts_is_none(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    try:
        gitdir = Path(repo.git_dir)
        (gitdir / "rebase-merge").mkdir()
        snapshot = observe_operation_snapshot(tmp_git_repo, target="main")
    finally:
        repo.close()

    assert rebase_in_progress_at(tmp_git_repo)
    assert snapshot.operation_state is OperationState.NONE
    assert snapshot.unmerged_paths == ()
    assert snapshot.rebase_head is None


def test_snapshot_dataclass_is_frozen() -> None:
    snapshot = OperationSnapshot(
        unmerged_paths=(),
        unmerged_query_ok=True,
        operation_state=OperationState.NONE,
        head_sha=None,
        target_tip=None,
        merge_head=None,
        rebase_head=None,
        index_tree=None,
        staged_conflict_marker_paths=(),
    )
    with pytest.raises(FrozenInstanceError):
        _assign_operation_state(snapshot, OperationState.UNKNOWN)
