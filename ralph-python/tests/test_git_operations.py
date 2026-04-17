"""Unit tests for git operations."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from git import Repo

from ralph.git.operations import (
    GitOperationError,
    append_to_gitignore,
    create_commit,
    find_repo_root,
    get_current_branch,
    get_head_sha,
    has_staged_changes,
    is_repo_clean,
    merge_base,
    push,
    stage_all,
)

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


def test_stage_all(tmp_git_repo: Path) -> None:
    """Test staging all changes."""
    readme = tmp_git_repo / "README.md"
    readme.write_text("updated content")

    stage_all(tmp_git_repo)

    repo = Repo(tmp_git_repo)
    staged = repo.index.diff("HEAD")
    assert len(staged) > 0


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

    class FakeIndex:
        def commit(self, message: str, author, committer) -> FakeCommit:
            captured["message"] = message
            captured["author"] = author
            captured["committer"] = committer
            return FakeCommit()

    fake_repo = SimpleNamespace(index=FakeIndex(), config_reader=lambda: FakeConfig())
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_args, **_kwargs: fake_repo)

    try:
        sha = create_commit(Path("/tmp/repo"), "Test commit message")
    finally:
        monkeypatch.undo()

    assert sha == "a" * FULL_SHA_LENGTH
    assert captured["message"] == "Test commit message"


def test_create_commit_with_author() -> None:
    """Test creating a commit with custom author."""
    captured: dict[str, object] = {}

    class FakeCommit:
        hexsha = "b" * FULL_SHA_LENGTH

    class FakeIndex:
        def commit(self, message: str, author, committer) -> FakeCommit:
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

    author = captured["author"]
    assert sha == "b" * FULL_SHA_LENGTH
    assert captured["message"] == "Custom author commit"
    assert author.name == "Custom User"
    assert author.email == "custom@example.com"


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
    # Create a new branch
    repo = Repo(tmp_git_repo)
    repo.create_head("test-branch")

    with pytest.raises(GitOperationError):
        push(tmp_git_repo, remote="origin", branch="test-branch")
