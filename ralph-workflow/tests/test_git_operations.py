"""Unit tests for git operations."""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from git import Actor, GitCommandError, Repo

from ralph.git.git_run_result import GitRunResult
from ralph.git.operations import (
    GitOperationError,
    append_to_gitignore,
    create_commit,
    find_repo_root,
    get_current_branch,
    get_head_sha,
    has_commits_since,
    has_staged_changes,
    has_uncommitted_changes,
    is_repo_clean,
    merge_base,
    push,
    stage_all,
)
from ralph.git.subprocess_runner import run_git

# Real-git tests fork `git` subprocesses; under full-suite worksteal
# parallelism the default 1s wall-clock alarm intermittently fires on a
# loaded machine even though each test normally finishes in ~100ms.
pytestmark = pytest.mark.timeout_seconds(5)

# Most tests in this module exercise real git operations against the
# ``tmp_git_repo`` fixture (per-test process-isolated git repository).
# Wall-clock cost under parallel xdist load is regularly > 1 s on busy
# machines, so the default 1-second per-test ceiling is unsafe. A few
# tests that do not touch the fixture complete in < 1 s and tolerate
# the elevated ceiling as a no-op.
pytestmark = pytest.mark.timeout_seconds(5)

FULL_SHA_LENGTH = 40
INITIAL_OCCURRENCE_COUNT = 1
DEFAULT_BRANCHES = {"main", "master"}


def test_find_repo_root(tmp_git_repo: Path) -> None:
    """Test finding repository root."""
    root = find_repo_root(tmp_git_repo)
    assert root == tmp_git_repo


def test_find_repo_root_prefers_active_worktree_root(tmp_path: Path) -> None:
    """Worktree paths should resolve to the active worktree, not the main checkout."""
    worktree = tmp_path / "feature-worktree"
    fake_repo = SimpleNamespace(
        working_tree_dir=str(worktree),
        working_dir=str(tmp_path / "main"),
    )
    nested = worktree / "src"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_args, **_kwargs: fake_repo)

    try:
        assert find_repo_root(nested) == worktree.resolve()
    finally:
        monkeypatch.undo()


def test_find_repo_root_not_git() -> None:
    """Test finding repo root when not in git repository."""
    with pytest.raises(GitOperationError, match="Not inside a git repository"):
        find_repo_root(Path("/tmp"))


def test_is_repo_clean(tmp_git_repo: Path) -> None:
    """Test checking if repository is clean."""
    assert is_repo_clean(tmp_git_repo) is True

    # Make a change
    readme = tmp_git_repo / "README.md"
    readme.write_text("updated content")

    assert is_repo_clean(tmp_git_repo) is False


def test_is_repo_clean_prefers_bounded_subprocess_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ralph.git.operations.run_git",
        lambda args, *, cwd, label: GitRunResult(
            args=("git", *args),
            returncode=0,
            stdout=" M README.md\n",
            stderr="",
        ),
    )
    monkeypatch.setattr(
        "ralph.git.operations.Repo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Repo fallback should not run")
        ),
    )

    assert is_repo_clean(Path("/tmp/repo")) is False


def test_has_staged_changes() -> None:
    """Test checking for staged changes."""
    clean_repo = SimpleNamespace(
        index=SimpleNamespace(diff=lambda _ref: []),
        untracked_files=[],
    )
    dirty_repo = SimpleNamespace(
        index=SimpleNamespace(diff=lambda _ref: [object()]),
        untracked_files=[],
    )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_args, **_kwargs: clean_repo)
    try:
        assert has_staged_changes(Path("/tmp/repo")) is False
        monkeypatch.setattr("ralph.git.operations.Repo", lambda *_args, **_kwargs: dirty_repo)
        assert has_staged_changes(Path("/tmp/repo")) is True
    finally:
        monkeypatch.undo()


def test_has_uncommitted_changes_clean_repo(tmp_git_repo: Path) -> None:
    assert has_uncommitted_changes(tmp_git_repo) is False


def test_has_uncommitted_changes_dirty_repo(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "README.md").write_text("dirty")
    assert has_uncommitted_changes(tmp_git_repo) is True


def test_has_uncommitted_changes_untracked_file(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "new_file.txt").write_text("new")
    assert has_uncommitted_changes(tmp_git_repo) is True


def test_has_uncommitted_changes_prefers_subprocess_git_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ralph.git.operations.run_git",
        lambda args, *, cwd, label: GitRunResult(
            args=("git", *args),
            returncode=0,
            stdout=" M README.md\n",
            stderr="",
        ),
    )
    monkeypatch.setattr(
        "ralph.git.operations.Repo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Repo fallback should not run")
        ),
    )

    assert has_uncommitted_changes(Path("/tmp/repo")) is True


def test_has_commits_since_no_baseline_returns_true(tmp_git_repo: Path) -> None:
    assert has_commits_since(tmp_git_repo, None) is True


def test_has_commits_since_head_equals_baseline_returns_false(tmp_git_repo: Path) -> None:
    head = get_head_sha(tmp_git_repo)
    assert has_commits_since(tmp_git_repo, head) is False


def test_has_commits_since_prefers_bounded_subprocess_rev_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ralph.git.operations.run_git",
        lambda args, *, cwd, label: GitRunResult(
            args=("git", *args),
            returncode=0,
            stdout="",
            stderr="",
        ),
    )
    monkeypatch.setattr(
        "ralph.git.operations.Repo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Repo fallback should not run")
        ),
    )

    assert has_commits_since(Path("/tmp/repo"), "abc123") is False


def test_has_commits_since_new_commit_returns_true(tmp_git_repo: Path) -> None:
    baseline = get_head_sha(tmp_git_repo)
    (tmp_git_repo / "extra.txt").write_text("more")
    add_result = run_git(("add", "extra.txt"), cwd=tmp_git_repo, label="git-add-test")
    assert add_result.returncode == 0
    commit_result = run_git(
        ("commit", "-m", "another commit"),
        cwd=tmp_git_repo,
        label="git-commit-test",
    )
    assert commit_result.returncode == 0
    assert has_commits_since(tmp_git_repo, baseline) is True


def test_stage_all(tmp_git_repo: Path) -> None:
    """Test staging all changes."""
    readme = tmp_git_repo / "README.md"
    readme.write_text("updated content")

    stage_all(tmp_git_repo)

    repo = Repo(tmp_git_repo)
    try:
        staged = repo.index.diff("HEAD")
        assert len(staged) > 0
    finally:
        repo.close()


def test_stage_all_recovers_from_stale_index_lock(tmp_git_repo: Path) -> None:
    lock_path = tmp_git_repo / ".git" / "index.lock"
    lock_path.write_text("locked", encoding="utf-8")
    stale_time = time.time() - 60
    os.utime(lock_path, (stale_time, stale_time))

    calls = {"count": 0}

    class FakeGit:
        def add(self, *args: object, **kwargs: object) -> None:
            calls["count"] += 1
            if calls["count"] == 1:
                raise GitCommandError(
                    ["git", "add", "-A"],
                    128,
                    stderr=(
                        f"fatal: Unable to create '{lock_path}': File exists.\n\n"
                        "Another git process seems to be running in this repository"
                    ),
                )

    fake_repo = SimpleNamespace(git=FakeGit(), close=lambda: None)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_args, **_kwargs: fake_repo)

    try:
        stage_all(tmp_git_repo)
    finally:
        monkeypatch.undo()

    assert calls["count"] == 2
    assert not lock_path.exists()


def test_create_commit() -> None:
    """Test creating a commit."""
    captured: dict[str, object] = {}

    class FakeCommit:
        hexsha = "a" * FULL_SHA_LENGTH

    class FakeConfig:
        def get_value(self, section: str, key: str, default: str) -> str:
            if (section, key) == ("user", "name"):
                return "Test User"
            if (section, key) == ("user", "email"):
                return "test@example.com"
            return default

    def fake_config_reader() -> FakeConfig:
        return FakeConfig()

    class FakeIndex:
        def commit(self, message: str, author: object, committer: object) -> FakeCommit:
            captured["message"] = message
            captured["author"] = author
            captured["committer"] = committer
            return FakeCommit()

    fake_repo = SimpleNamespace(index=FakeIndex(), config_reader=fake_config_reader)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_args, **_kwargs: fake_repo)

    try:
        sha = create_commit(Path("/tmp/repo"), "Test commit message")
    finally:
        monkeypatch.undo()

    assert sha == "a" * FULL_SHA_LENGTH
    assert captured["message"] == (
        "Test commit message\n\nCo-authored-by: Ralph Workflow <noreply@ralphworkflow.com>"
    )


def test_create_commit_recovers_from_stale_index_lock(tmp_git_repo: Path) -> None:
    lock_path = tmp_git_repo / ".git" / "index.lock"
    lock_path.write_text("locked", encoding="utf-8")
    stale_time = time.time() - 60
    os.utime(lock_path, (stale_time, stale_time))
    calls = {"count": 0}

    class FakeCommit:
        hexsha = "c" * FULL_SHA_LENGTH

    class FakeConfig:
        def get_value(self, section: str, key: str, default: str) -> str:
            if (section, key) == ("user", "name"):
                return "Test User"
            if (section, key) == ("user", "email"):
                return "test@example.com"
            return default

    class FakeIndex:
        def commit(self, message: str, author: object, committer: object) -> FakeCommit:
            del message, author, committer
            calls["count"] += 1
            if calls["count"] == 1:
                raise GitCommandError(
                    ["git", "commit", "-m", "Test commit message"],
                    128,
                    stderr=(
                        f"fatal: Unable to create '{lock_path}': File exists.\n\n"
                        "Another git process seems to be running in this repository"
                    ),
                )
            return FakeCommit()

    def fake_config_reader() -> FakeConfig:
        return FakeConfig()

    fake_repo = SimpleNamespace(
        index=FakeIndex(),
        config_reader=fake_config_reader,
        close=lambda: None,
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_args, **_kwargs: fake_repo)

    try:
        sha = create_commit(Path("/tmp/repo"), "Test commit message")
    finally:
        monkeypatch.undo()

    assert sha == "c" * FULL_SHA_LENGTH
    assert calls["count"] == 2
    assert not lock_path.exists()


def test_create_commit_with_author() -> None:
    """Test creating a commit with custom author."""
    captured: dict[str, object] = {}

    class FakeCommit:
        hexsha = "b" * FULL_SHA_LENGTH

    class FakeIndex:
        def commit(self, message: str, author: object, committer: object) -> FakeCommit:
            captured["message"] = message
            captured["author"] = author
            captured["committer"] = committer
            return FakeCommit()

    fake_repo = SimpleNamespace(index=FakeIndex(), config_reader=lambda: None)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_args, **_kwargs: fake_repo)

    try:
        sha = create_commit(
            Path("/tmp/repo"),
            "Custom author commit",
            author_name="Custom User",
            author_email="custom@example.com",
        )
    finally:
        monkeypatch.undo()

    author = cast("Actor", captured["author"])
    assert sha == "b" * FULL_SHA_LENGTH
    assert captured["message"] == (
        "Custom author commit\n\nCo-authored-by: Ralph Workflow <noreply@ralphworkflow.com>"
    )
    assert author.name == "Custom User"
    assert author.email == "custom@example.com"


def test_create_commit_appends_ralph_workflow_coauthor_trailer() -> None:
    """Generated commits keep the repo identity and add Ralph Workflow as co-author."""
    captured: dict[str, object] = {}

    class FakeCommit:
        hexsha = "d" * FULL_SHA_LENGTH

    class FakeConfig:
        def get_value(self, section: str, key: str, default: str) -> str:
            if (section, key) == ("user", "name"):
                return "Repo User"
            if (section, key) == ("user", "email"):
                return "repo@example.com"
            return default

    def fake_config_reader() -> FakeConfig:
        return FakeConfig()

    class FakeIndex:
        def commit(self, message: str, author: object, committer: object) -> FakeCommit:
            captured["message"] = message
            captured["author"] = author
            captured["committer"] = committer
            return FakeCommit()

    fake_repo = SimpleNamespace(index=FakeIndex(), config_reader=fake_config_reader)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_args, **_kwargs: fake_repo)

    try:
        sha = create_commit(Path("/tmp/repo"), "feat(cli): support generated commits")
    finally:
        monkeypatch.undo()

    author = cast("Actor", captured["author"])
    assert sha == "d" * FULL_SHA_LENGTH
    assert captured["message"] == (
        "feat(cli): support generated commits\n\n"
        "Co-authored-by: Ralph Workflow <noreply@ralphworkflow.com>"
    )
    assert author.name == "Repo User"
    assert author.email == "repo@example.com"


def test_get_head_sha(tmp_git_repo: Path) -> None:
    """Test getting HEAD SHA."""
    sha = get_head_sha(tmp_git_repo)
    assert len(sha) == FULL_SHA_LENGTH


def test_get_current_branch(tmp_git_repo: Path) -> None:
    """Test getting current branch name."""
    branch = get_current_branch(tmp_git_repo)
    assert branch in DEFAULT_BRANCHES


def test_append_to_gitignore(tmp_git_repo: Path) -> None:
    """Test appending patterns to .gitignore."""
    patterns = [".agent/", "*.log", "__pycache__/"]
    append_to_gitignore(tmp_git_repo, patterns)

    gitignore = tmp_git_repo / ".gitignore"
    content = gitignore.read_text()

    for pattern in patterns:
        assert pattern in content


def test_append_to_gitignore_existing(tmp_git_repo: Path) -> None:
    """Test appending to existing .gitignore without duplicates."""
    gitignore = tmp_git_repo / ".gitignore"
    gitignore.write_text(".existing\n")

    patterns = [".new/", ".existing"]
    append_to_gitignore(tmp_git_repo, patterns)

    content = gitignore.read_text()
    # .existing should not be duplicated
    assert content.count(".existing") == INITIAL_OCCURRENCE_COUNT
    assert ".new/" in content


def test_merge_base() -> None:
    """Test finding merge base between commits."""
    fake_base = SimpleNamespace(hexsha="c" * FULL_SHA_LENGTH)

    class FakeRepo:
        def merge_base(self, ref_a: str, ref_b: str) -> list[SimpleNamespace]:
            assert {ref_a, ref_b} == {"sha1", "sha2"}
            return [fake_base]

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_args, **_kwargs: FakeRepo())

    try:
        base = merge_base(Path("/tmp/repo"), "sha1", "sha2")
        base2 = merge_base(Path("/tmp/repo"), "sha2", "sha1")
    finally:
        monkeypatch.undo()

    assert base == fake_base.hexsha
    assert base == base2


def test_push_without_remote(tmp_git_repo: Path) -> None:
    """Test that push fails gracefully without remote."""
    repo = Repo(tmp_git_repo)
    try:
        repo.create_head("test-branch")
    finally:
        repo.close()

    with pytest.raises(GitOperationError):
        push(tmp_git_repo, remote="no-such-remote", branch="test-branch")
