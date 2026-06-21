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
    _atomic_append_text,
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


# --- Phase 5 edge-case tests for _atomic_append_text ---
#
# Each test pins one observable behavior of the atomic helper. The names
# follow the plan: test_atomic_append_text_<behavior> so the test id maps
# directly to the staging-filename contract pinned by the audit invariant.


@pytest.mark.timeout_seconds(5)
def test_atomic_append_text_empty_payload_is_noop(tmp_path: Path) -> None:
    """Empty payload is a no-op: no exception, no staging file remains.

    The helper must accept an empty payload and complete without raising
    or leaving a dangling staging sibling. The boundary check
    (``existing ends with \\n``) is also a no-op because the staging
    write_text and Path.replace are both invoked, but the content is
    identical to the existing file -- the helper should still clean up
    the staging file via the BaseException-suppress block.
    """
    target = tmp_path / "empty_payload.txt"
    target.write_text("initial\n", encoding="utf-8")
    pre_bytes = target.read_bytes()

    _atomic_append_text(target, "", encoding="utf-8")

    assert target.read_bytes() == pre_bytes, (
        "Empty payload must NOT modify the target file"
    )
    staging_siblings = [
        p for p in target.parent.iterdir()
        if p.name.startswith(target.name) and ".ralph-staging." in p.name
    ]
    assert not staging_siblings, (
        f"Empty payload must NOT leave a staging sibling, found: "
        f"{[str(s) for s in staging_siblings]}"
    )


@pytest.mark.timeout_seconds(5)
def test_atomic_append_text_preserves_crlf_in_existing_content(tmp_path: Path) -> None:
    """CRLF-terminated existing content is preserved byte-for-byte through the atomic round-trip.

    Pins the byte-preserving contract: a target file containing
    ``b"line-one\\r\\nline-two\\r\\n"`` followed by an append of
    ``"line-three\\n"`` must publish
    ``b"line-one\\r\\nline-two\\r\\nline-three\\n"`` -- every CRLF
    in the existing content is preserved, the appended LF does NOT
    get converted, and the boundary insert (if any) is byte-accurate.

    The helper reads via ``Path.read_bytes()`` and writes via
    ``Path.write_bytes()`` so the CRLF byte sequence ``\\r\\n`` is
    passed through unchanged. A text-mode round trip via
    ``read_text``/``write_text`` would normalize CRLF to LF on POSIX
    (universal-newlines mode) and silently corrupt the source. A future
    refactor that regresses to text mode surfaces here as a CRLF
    byte-presence failure.
    """
    target = tmp_path / "crlf_existing.txt"
    target.write_bytes(b"line-one\r\nline-two\r\n")

    _atomic_append_text(target, "line-three\n", encoding="utf-8")

    raw_bytes = target.read_bytes()
    assert raw_bytes == b"line-one\r\nline-two\r\nline-three\n", (
        f"CRLF-terminated existing content must be preserved byte-for-byte. "
        f"Expected: b'line-one\\r\\nline-two\\r\\nline-three\\n', got: {raw_bytes!r}"
    )
    assert raw_bytes.count(b"\r\n") == 2, (
        f"Both CRLF terminators must be present in the published bytes, "
        f"got: {raw_bytes!r}"
    )
    raw_text = target.read_text(encoding="utf-8")
    assert "line-one" in raw_text, "First line text must be preserved"
    assert "line-two" in raw_text, "Second line text must be preserved"
    assert "line-three" in raw_text, "Appended payload must be present in output"


@pytest.mark.timeout_seconds(5)
def test_atomic_append_text_inserts_separator_when_existing_lacks_trailing_newline(
    tmp_path: Path,
) -> None:
    """When the existing file lacks a trailing newline, the helper inserts one.

    Pins the boundary normalization branch: a file containing
    ``"existing-without-newline"`` followed by a payload of
    ``"*.cache\\n"`` must publish ``"existing-without-newline\\n*.cache"``
    so the two rules are separate lines. The helper's separator logic
    is what the audit-invariant comment in
    ``ralph/git/operations.py`` calls out.
    """
    target = tmp_path / "no_trailing_newline.txt"
    target.write_text("existing-without-newline", encoding="utf-8")

    _atomic_append_text(target, "*.cache\n", encoding="utf-8")

    raw = target.read_text(encoding="utf-8")
    assert "existing-without-newline\n*.cache" in raw, (
        f"Boundary must contain a newline so the two rules are separate lines; "
        f"got: {raw!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_atomic_append_text_propagates_oserror_when_target_is_directory(
    tmp_path: Path,
) -> None:
    """When the target path is a directory, the underlying OSError must propagate.

    The helper uses ``Path.read_text()`` to load existing content and
    ``Path.write_text()`` to publish the staged file -- both fail with
    ``OSError`` (specifically ``IsADirectoryError`` on POSIX, subclass of
    ``OSError``) when the path resolves to a directory. The helper does
    NOT swallow ``OSError``; the failure surfaces to the caller.
    """
    target = tmp_path / "im_a_directory"
    target.mkdir()

    assert target.is_dir()
    with pytest.raises(OSError):
        _atomic_append_text(target, "payload\n", encoding="utf-8")


@pytest.mark.timeout_seconds(5)
def test_atomic_append_text_replaces_target_symlink(tmp_path: Path) -> None:
    """When the target path is a symlink, ``Path.replace`` REPLACES the symlink.

    The helper stages the new content in a sibling file (under the
    target's parent, NOT the symlink target's directory) and then
    ``Path.replace(staging, target)`` -- on POSIX, ``Path.replace`` on
    a symlink replaces the symlink itself (NOT the symlink target's
    file). This is the documented pathlib behavior.

    The test pins that the staging step uses the target's parent
    directory (NOT the symlink resolution), and the replace step
    replaces the symlink (NOT the symlink target's file).
    """
    real_dir = tmp_path / "real_target_dir"
    real_dir.mkdir()
    real_file = real_dir / "real.txt"
    real_file.write_text("REAL content\n")

    symlink_dir = tmp_path / "symlink_dir"
    symlink_dir.symlink_to(real_dir)

    target_via_symlink = symlink_dir / "via_symlink.txt"
    target_via_symlink.symlink_to(real_file)
    assert target_via_symlink.is_symlink()

    _atomic_append_text(target_via_symlink, "appended\n", encoding="utf-8")

    # The real file is NOT modified.
    assert real_file.read_text(encoding="utf-8") == "REAL content\n", (
        "Path.replace on a symlink must NOT follow the symlink and write to "
        "the target's underlying file"
    )
    # The symlink itself may now be a regular file (replace replaced it).
    assert not target_via_symlink.is_symlink(), (
        "Path.replace must have replaced the symlink with a regular file"
    )
    assert target_via_symlink.read_text(encoding="utf-8") == "REAL content\nappended\n", (
        "New regular file at the symlink's path must contain the published content"
    )

