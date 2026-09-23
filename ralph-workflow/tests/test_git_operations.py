"""Unit tests for git operations."""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from git import GitCommandError, Repo

from ralph.git.commit_result import CommitCreationStatus
from ralph.git.errors import GitOperationError
from ralph.git.git_run_result import GitRunResult
from ralph.git.operations import (
    _git_status_porcelain_lines,
    _recover_stale_git_lock,
    append_to_gitignore,
    create_commit,
    find_repo_root,
    get_current_branch,
    get_head_sha,
    has_commits_since,
    has_staged_changes,
    has_uncommitted_changes,
    is_repo_clean,
    list_changed_paths,
    merge_base,
    push,
    stage_all,
)
from ralph.pipeline.effect_router import determine_effect_from_policy
from ralph.pipeline.effects import EmptyCommitEffect
from ralph.pipeline.state import CommitState, PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope


def _unused_pid() -> int:
    """Return a PID that is overwhelmingly likely to not be running.

    PIDs cycle quickly, so a pid equal to ``os.getpid() + 1_000_000`` is
    almost certainly not alive by the time the test runs. We do NOT use a
    fixed integer like 999999 because some CI environments pre-allocate
    PIDs in low ranges.
    """
    return os.getpid() + 1_000_000


def _make_index_lock_error(lock_path: Path) -> GitCommandError:
    return GitCommandError(
        ["git", "add", "-A"],
        128,
        stderr=f"fatal: Unable to create '{lock_path}': File exists.\n\n"
        "Another git process seems to be running in this repository",
    )


FULL_SHA_LENGTH = 40
INITIAL_OCCURRENCE_COUNT = 1
DEFAULT_BRANCHES = {"main", "master"}


@pytest.mark.subprocess_e2e
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


@pytest.mark.subprocess_e2e
def test_find_repo_root_not_git() -> None:
    """Test finding repo root when not in git repository."""
    with pytest.raises(GitOperationError, match="Not inside a git repository"):
        find_repo_root(Path("/tmp"))


def test_is_repo_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful porcelain status determines clean state without Git I/O."""
    outputs = iter(("", " M README.md\n"))
    monkeypatch.setattr(
        "ralph.git.operations.run_git",
        lambda args, *, cwd, label: GitRunResult(
            args=("git", *args), returncode=0, stdout=next(outputs), stderr=""
        ),
    )

    assert is_repo_clean(Path("/tmp/repo")) is True
    assert is_repo_clean(Path("/tmp/repo")) is False


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


@pytest.mark.subprocess_e2e
def test_has_uncommitted_changes_clean_repo(tmp_git_repo: Path) -> None:
    assert has_uncommitted_changes(tmp_git_repo) is False


@pytest.mark.subprocess_e2e
def test_has_uncommitted_changes_dirty_repo(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "README.md").write_text("dirty")
    assert has_uncommitted_changes(tmp_git_repo) is True


@pytest.mark.subprocess_e2e
def test_has_uncommitted_changes_untracked_file(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "new_file.txt").write_text("new")
    assert has_uncommitted_changes(tmp_git_repo) is True


@pytest.mark.subprocess_e2e
@pytest.mark.parametrize(
    ("ignore_path", "ignored_path"),
    ((".gitignore", "ignored.log"), (".git/info/exclude", "excluded.log")),
)
def test_ignored_only_commit_phase_selects_empty_effect(
    tmp_git_repo: Path, ignore_path: str, ignored_path: str
) -> None:
    (tmp_git_repo / ignore_path).write_text(f"{ignored_path}\n", encoding="utf-8")
    if ignore_path == ".gitignore":
        with Repo(tmp_git_repo) as repo:
            repo.index.add([ignore_path])
            repo.index.commit("add ignored fixture")
    (tmp_git_repo / ignored_path).write_text("ignored\n", encoding="utf-8")
    defaults = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy_bundle = load_policy(defaults)
    state = PipelineState(phase="development_commit", commit=CommitState(agent_invoked=False))

    selected = determine_effect_from_policy(
        state,
        policy_bundle,
        WorkspaceScope(root=tmp_git_repo, allowed_roots=[tmp_git_repo]),
    )

    assert isinstance(selected, EmptyCommitEffect)


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


@pytest.mark.subprocess_e2e
def test_has_commits_since_no_baseline_returns_true(tmp_git_repo: Path) -> None:
    assert has_commits_since(tmp_git_repo, None) is True


def test_has_commits_since_head_equals_baseline_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty successful ``rev-list`` result reports no newer commit without Git I/O."""
    monkeypatch.setattr(
        "ralph.git.operations.run_git",
        lambda args, *, cwd, label: GitRunResult(
            args=("git", *args), returncode=0, stdout="", stderr=""
        ),
    )

    assert has_commits_since(Path("/tmp/repo"), "a" * FULL_SHA_LENGTH) is False


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


def test_has_commits_since_new_commit_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """A nonempty bounded ``rev-list`` result reports a newer commit without Git I/O."""
    monkeypatch.setattr(
        "ralph.git.operations.run_git",
        lambda args, *, cwd, label: GitRunResult(
            args=("git", *args),
            returncode=0,
            stdout="a" * FULL_SHA_LENGTH + "\n",
            stderr="",
        ),
    )

    assert has_commits_since(Path("/tmp/repo"), "b" * FULL_SHA_LENGTH) is True


def test_stage_all() -> None:
    """Staging delegates to GitPython's all-files operation without Git I/O."""
    calls: list[bool] = []

    class FakeGit:
        def add(self, **kwargs: bool) -> str:
            calls.append(kwargs["A"])
            return ""

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "ralph.git.operations.Repo",
        lambda *_args, **_kwargs: SimpleNamespace(git=FakeGit(), close=lambda: None),
    )
    try:
        stage_all(Path("/tmp/repo"))
    finally:
        monkeypatch.undo()

    assert calls == [True]


@pytest.mark.subprocess_e2e
def test_stage_all_recovers_from_stale_index_lock(tmp_git_repo: Path) -> None:
    lock_path = tmp_git_repo / ".git" / "index.lock"
    lock_path.write_text(str(_unused_pid()), encoding="utf-8")
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
    fake_repo.head = SimpleNamespace(commit=SimpleNamespace(hexsha="a" * FULL_SHA_LENGTH))
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_args, **_kwargs: fake_repo)

    try:
        result = create_commit(
            Path("/tmp/repo"), "Test commit message", expected_head="a" * FULL_SHA_LENGTH
        )
    finally:
        monkeypatch.undo()

    assert result.status is CommitCreationStatus.CREATED
    assert result.sha == "a" * FULL_SHA_LENGTH
    assert captured["message"] == (
        "Test commit message\n\nCo-authored-by: Ralph Workflow <noreply@ralphworkflow.com>"
    )


def test_create_commit_expected_head_mismatch_returns_already_advanced() -> None:
    current_head = "e" * FULL_SHA_LENGTH

    class FakeIndex:
        def commit(self, **_kwargs: object) -> object:
            raise AssertionError("commit must not run after HEAD changed")

    fake_repo = SimpleNamespace(
        head=SimpleNamespace(commit=SimpleNamespace(hexsha=current_head)),
        index=FakeIndex(),
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_args, **_kwargs: fake_repo)
    try:
        result = create_commit(
            Path("/tmp/repo"), "Test commit", expected_head="f" * FULL_SHA_LENGTH
        )
    finally:
        monkeypatch.undo()

    assert result.status is CommitCreationStatus.ALREADY_ADVANCED
    assert result.sha == current_head


@pytest.mark.subprocess_e2e
def test_create_commit_recovers_from_stale_index_lock(tmp_git_repo: Path) -> None:
    lock_path = tmp_git_repo / ".git" / "index.lock"
    lock_path.write_text(str(_unused_pid()), encoding="utf-8")
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
        head=SimpleNamespace(commit=SimpleNamespace(hexsha="a" * FULL_SHA_LENGTH)),
        close=lambda: None,
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_args, **_kwargs: fake_repo)

    try:
        result = create_commit(
            Path("/tmp/repo"), "Test commit message", expected_head="a" * FULL_SHA_LENGTH
        )
    finally:
        monkeypatch.undo()

    assert result.sha == "c" * FULL_SHA_LENGTH
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

    fake_repo = SimpleNamespace(
        index=FakeIndex(),
        config_reader=lambda: None,
        head=SimpleNamespace(commit=SimpleNamespace(hexsha="a" * FULL_SHA_LENGTH)),
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_args, **_kwargs: fake_repo)

    try:
        result = create_commit(
            Path("/tmp/repo"),
            "Custom author commit",
            author_name="Custom User",
            author_email="custom@example.com",
            expected_head="a" * FULL_SHA_LENGTH,
        )
    finally:
        monkeypatch.undo()

    author = captured["author"]
    assert result.sha == "b" * FULL_SHA_LENGTH
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

    fake_repo = SimpleNamespace(
        index=FakeIndex(),
        config_reader=fake_config_reader,
        head=SimpleNamespace(commit=SimpleNamespace(hexsha="a" * FULL_SHA_LENGTH)),
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_args, **_kwargs: fake_repo)

    try:
        result = create_commit(
            Path("/tmp/repo"),
            "feat(cli): support generated commits",
            expected_head="a" * FULL_SHA_LENGTH,
        )
    finally:
        monkeypatch.undo()

    author = captured["author"]
    assert result.sha == "d" * FULL_SHA_LENGTH
    assert captured["message"] == (
        "feat(cli): support generated commits\n\n"
        "Co-authored-by: Ralph Workflow <noreply@ralphworkflow.com>"
    )
    assert author.name == "Repo User"
    assert author.email == "repo@example.com"


def test_get_head_sha() -> None:
    """HEAD reads return the commit SHA through the GitPython boundary."""
    expected = "a" * FULL_SHA_LENGTH
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "ralph.git.operations.Repo",
        lambda *_args, **_kwargs: SimpleNamespace(
            head=SimpleNamespace(commit=SimpleNamespace(hexsha=expected)),
            close=lambda: None,
        ),
    )
    try:
        assert get_head_sha(Path("/tmp/repo")) == expected
    finally:
        monkeypatch.undo()


@pytest.mark.subprocess_e2e
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


def test_push_without_remote() -> None:
    """An absent remote raises the public push error without a Git subprocess."""
    fake_repo = SimpleNamespace(
        remote=lambda _name: (_ for _ in ()).throw(ValueError("no such remote")),
        close=lambda: None,
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("ralph.git.operations.Repo", lambda *_args, **_kwargs: fake_repo)
    try:
        with pytest.raises(GitOperationError, match="no such remote"):
            push(Path("/tmp/repo"), remote="no-such-remote", branch="test-branch")
    finally:
        monkeypatch.undo()


# ---------------------------------------------------------------------------
# Regression coverage for analysis-feedback correctness holes in the touched
# git-operations surface:
#   1. _recover_stale_git_lock must NOT auto-unlink a Git lock file based on
#      age alone -- unlinking a live lock can corrupt a long-running Git
#      operation. Recovery must be gated on a stronger stale-proof (the PID
#      stored in the lock file, when present, must no longer be running).
#   2. _git_status_porcelain_lines must NOT silently collapse a non-zero
#      ``git status --porcelain`` return into ``[]`` -- that hides real
#      failures and makes ``has_staged_changes`` / ``list_changed_paths`` /
#      ``get_staged_files`` report a clean repo.
#   3. has_staged_changes must report ONLY staged changes -- an
#      untracked-only worktree must return False, and the
#      ``No staged changes to commit`` CLI message must continue to be the
#      user-facing truth.
# ---------------------------------------------------------------------------


def test_recover_stale_git_lock_fails_closed_when_lock_has_no_pid(
    tmp_path: Path,
) -> None:
    """A lock file with non-numeric content is NOT auto-unlinked.

    Git writes the holding PID into ``index.lock``/``HEAD.lock``/etc. When
    the lock content is not a parseable PID, the helper cannot prove the
    lock is stale (the writing process could still be alive but not have
    written a PID for some reason). The helper must fail closed: do NOT
    unlink, return False, and surface the contention as a normal error.
    """
    lock_path = tmp_path / "index.lock"
    lock_path.write_text("not-a-pid", encoding="utf-8")
    stale_time = time.time() - 60
    os.utime(lock_path, (stale_time, stale_time))

    error = _make_index_lock_error(lock_path)

    assert _recover_stale_git_lock("stage_all", error) is False, (
        "Lock with no parseable PID must NOT be auto-unlinked (fail-closed)"
    )
    assert lock_path.exists(), "Lock file must remain on disk when recovery fails"


def test_recover_stale_git_lock_fails_closed_when_lock_pid_is_alive(
    tmp_path: Path,
) -> None:
    """A lock file with a still-alive PID is NOT auto-unlinked.

    Even when the lock file is older than the stale threshold, if the PID
    recorded in the lock is still running the lock is live and must NOT be
    unlinked. A legitimate long-running ``git gc`` or ``git fetch`` could
    have written that PID a moment ago.
    """
    lock_path = tmp_path / "index.lock"
    current_pid = os.getpid()
    lock_path.write_text(str(current_pid), encoding="utf-8")
    stale_time = time.time() - 60
    os.utime(lock_path, (stale_time, stale_time))

    error = _make_index_lock_error(lock_path)

    assert _recover_stale_git_lock("stage_all", error) is False, (
        "Lock with still-alive PID must NOT be auto-unlinked (fail-closed)"
    )
    assert lock_path.exists(), "Lock file must remain on disk when PID is still alive"


def test_recover_stale_git_lock_recovers_when_lock_pid_is_dead(
    tmp_path: Path,
) -> None:
    """A lock file with a dead PID IS unlinked and the helper returns True.

    Strong stale-proof: when the PID stored in the lock file is no longer
    running, the lock is provably stale and safe to remove. The helper
    unlinks the file and returns True so the caller can retry.
    """
    lock_path = tmp_path / "index.lock"
    lock_path.write_text(str(_unused_pid()), encoding="utf-8")
    stale_time = time.time() - 60
    os.utime(lock_path, (stale_time, stale_time))

    error = _make_index_lock_error(lock_path)

    assert _recover_stale_git_lock("stage_all", error) is True, (
        "Lock with dead PID must be unlinked and recovery must succeed"
    )
    assert not lock_path.exists(), "Lock file must be unlinked when PID is dead"


def test_recover_stale_git_lock_returns_false_for_non_lock_error() -> None:
    """A non-lock GitCommandError does NOT trigger recovery.

    Errors that do not match the lock-path regex must return False so the
    caller re-raises the original exception.
    """
    error = GitCommandError(
        ["git", "status"],
        128,
        stderr="fatal: not a git repository: '.git'",
    )

    assert _recover_stale_git_lock("status", error) is False


def test_git_status_porcelain_lines_raises_on_nonzero_return(
    tmp_git_repo: Path,
) -> None:
    """A non-zero ``git status --porcelain`` return raises GitOperationError.

    Previously the helper silently returned ``[]``, which made
    ``has_staged_changes``/``list_changed_paths``/``get_staged_files``
    report a clean repo on real failures. The fix surfaces the failure so
    callers can route through their GitPython fallback (or fail loud).
    """

    def _broken_run_git(args: tuple[str, ...], *, cwd: Path, label: str) -> GitRunResult:
        return GitRunResult(
            args=("git", *args),
            returncode=128,
            stdout="",
            stderr="fatal: simulated git status failure",
        )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("ralph.git.operations.run_git", _broken_run_git)
    try:
        with pytest.raises(GitOperationError, match="git-status"):
            _git_status_porcelain_lines(tmp_git_repo)
    finally:
        monkeypatch.undo()


def test_list_changed_paths_includes_unstaged_and_untracked_scope_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-3: a commit artifact may scope paths before they are staged."""
    monkeypatch.setattr(
        "ralph.git.operations.run_git",
        lambda args, *, cwd, label: GitRunResult(
            args=("git", *args),
            returncode=0,
            stdout=" M src/modified.py\nA  src/staged.py\n?? src/untracked.py\n",
            stderr="",
        ),
    )

    assert list_changed_paths(Path("/tmp/repo")) == [
        "src/modified.py",
        "src/staged.py",
        "src/untracked.py",
    ]


def test_git_status_porcelain_lines_returns_lines_on_zero_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful bounded status call exposes its porcelain lines without Git I/O."""
    monkeypatch.setattr(
        "ralph.git.operations.run_git",
        lambda args, *, cwd, label: GitRunResult(
            args=("git", *args),
            returncode=0,
            stdout=" M README.md\n?? untracked.txt\n",
            stderr="",
        ),
    )

    assert _git_status_porcelain_lines(Path("/tmp/repo")) == [" M README.md", "?? untracked.txt"]


@pytest.mark.subprocess_e2e
def test_has_staged_changes_returns_false_for_untracked_only_repo(
    tmp_git_repo: Path,
) -> None:
    """An untracked-only worktree has NO staged changes.

    ``has_staged_changes`` is the API contract for "is there anything
    staged for the next commit?" -- untracked files do not satisfy that
    contract. The CLI message ``No staged changes to commit`` must remain
    accurate for an untracked-only repo.
    """
    untracked = tmp_git_repo / "untracked.txt"
    untracked.write_text("never staged", encoding="utf-8")

    assert has_staged_changes(tmp_git_repo) is False, (
        "Untracked-only worktree must NOT report staged changes; the "
        "staged-changes API is strictly about git-added files"
    )


@pytest.mark.subprocess_e2e
def test_has_staged_changes_returns_false_for_untracked_only_via_subprocess(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Untracked-only detection stays correct under the subprocess path.

    ``has_staged_changes`` prefers ``git status --porcelain`` when
    available. With ONLY untracked files in the worktree, the porcelain
    output is a single ``??`` line; the helper must return False even
    though it sees the untracked line.
    """
    untracked = tmp_git_repo / "untracked_only.txt"
    untracked.write_text("never staged", encoding="utf-8")

    # Sanity: the real ``git status --porcelain`` path returns the
    # expected untracked line, and the helper still returns False.
    assert has_staged_changes(tmp_git_repo) is False, (
        "Subprocess path: untracked-only worktree must return False"
    )
