from __future__ import annotations

from pathlib import Path

from git import GitCommandError, Repo

from ralph.git.git_command_queue import (
    WorktreeLease,
    WorktreeLeaseHolder,
    begin_agent_lease,
    try_acquire_lease,
)
from ralph.git.git_command_queue import (
    end_agent_lease as _end_worktree_lease,
)
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.timeout_defaults import GIT_SUBPROCESS_TIMEOUT_SECONDS

_GIT_OP_TIMEOUT_SECONDS = GIT_SUBPROCESS_TIMEOUT_SECONDS


def _git_config(repo: Repo, *args: str) -> str:
    cwd = Path(repo.working_tree_dir or repo.git_dir)
    result = run_git(
        ["config", *args],
        cwd=cwd,
        label="hooks-config",
        options=GitRunOptions(timeout=_GIT_OP_TIMEOUT_SECONDS),
    )
    if result.returncode != 0:
        raise GitCommandError(["git", "config", *args], result.returncode, stderr=result.stderr)
    return result.stdout


MARKER_FILENAME = "no_agent_commit"
TRACK_FILENAME = "git-wrapper-dir.txt"
HEAD_OID_FILENAME = "head-oid.txt"
HOOKS_STATE_FILENAME = "hooks-path-state"
HOOKS_DIR_NAME = "hooks"


class GitHelpers:
    real_git: Path | None
    wrapper_dir: Path | None
    wrapper_repo_root: Path | None
    worktree_lease: WorktreeLease | None

    def __init__(self) -> None:
        self.real_git = None
        self.wrapper_dir = None
        self.wrapper_repo_root = None
        self.worktree_lease = None


def start_agent_phase(repo_root: Path | str, helpers: GitHelpers | None = None) -> None:

    helpers = helpers or GitHelpers()
    helpers.wrapper_repo_root = Path(repo_root)
    lease = begin_agent_lease(
        repo_root,
        holder=WorktreeLeaseHolder(name="agent-phase"),
    )
    helpers.worktree_lease = lease
    repo = Repo(repo_root)
    try:
        ralph_dir = _ensure_ralph_dir(repo)
        helpers.wrapper_dir = ralph_dir

        _write_marker(ralph_dir)
        _write_track_file(ralph_dir)
        _capture_head_oid(repo, ralph_dir)
        _store_previous_hooks_path(repo, ralph_dir)
        _set_hooks_path(repo, ralph_dir)
    finally:
        repo.close()


def end_agent_phase(repo_root: Path | str, helpers: GitHelpers | None = None) -> None:
    helpers = helpers or GitHelpers()
    helpers.wrapper_repo_root = Path(repo_root)
    lease = helpers.worktree_lease
    if lease is None:
        lease = try_acquire_lease(repo_root, holder=WorktreeLeaseHolder(name="agent-phase"))
    repo = Repo(repo_root)
    try:
        ralph_dir = _ralph_dir_from_repo(repo)
        _restore_hooks_path(repo, ralph_dir)
        _remove_marker(ralph_dir)
        _remove_head_oid(ralph_dir)
        _remove_track_file(ralph_dir)
    finally:
        repo.close()
        if lease is not None:
            _end_worktree_lease(lease)
        helpers.worktree_lease = None


def detect_unauthorized_commit(repo_root: Path | str) -> bool:

    repo = Repo(repo_root)
    try:
        ralph_dir = _ralph_dir_from_repo(repo)
        head_file = ralph_dir / HEAD_OID_FILENAME
        if not head_file.exists():
            return False

        stored_oid = head_file.read_text().strip()
        if not stored_oid:
            return False

        try:
            current_head = repo.head.commit.hexsha
        except (ValueError, GitCommandError):
            return False

        return current_head != stored_oid
    finally:
        repo.close()


def _ensure_ralph_dir(repo: Repo) -> Path:
    git_dir = Path(repo.git_dir)
    ralph_dir = git_dir / "ralph"
    ralph_dir.mkdir(parents=True, exist_ok=True)
    return ralph_dir


def _ralph_dir_from_repo(repo: Repo) -> Path:
    return Path(repo.git_dir) / "ralph"


def _write_marker(ralph_dir: Path) -> None:
    write_text_if_changed(DEFAULT_FILE_BACKEND, ralph_dir / MARKER_FILENAME, "")


def _write_track_file(ralph_dir: Path) -> None:
    write_text_if_changed(DEFAULT_FILE_BACKEND, ralph_dir / TRACK_FILENAME, str(ralph_dir))


def _capture_head_oid(repo: Repo, ralph_dir: Path) -> None:
    try:
        oid = repo.head.commit.hexsha
    except (ValueError, GitCommandError):
        return

    write_text_if_changed(DEFAULT_FILE_BACKEND, ralph_dir / HEAD_OID_FILENAME, f"{oid}\n")


def _set_hooks_path(repo: Repo, ralph_dir: Path) -> None:
    hooks_dir = ralph_dir / HOOKS_DIR_NAME
    hooks_dir.mkdir(parents=True, exist_ok=True)
    _git_config(repo, "--local", "core.hooksPath", str(hooks_dir))


def _read_hooks_path(repo: Repo) -> str | None:
    try:
        value = _git_config(repo, "--local", "--get", "core.hooksPath")
    except GitCommandError as exc:
        if exc.status == 1:
            return None
        raise
    return value.strip()


def _store_previous_hooks_path(repo: Repo, ralph_dir: Path) -> None:
    state_path = ralph_dir / HOOKS_STATE_FILENAME
    if state_path.exists():
        return

    hooks_value = _read_hooks_path(repo)
    if hooks_value is None:
        write_text_if_changed(DEFAULT_FILE_BACKEND, state_path, "missing\n")
    else:
        write_text_if_changed(DEFAULT_FILE_BACKEND, state_path, f"value\n{hooks_value}\n")


def _restore_hooks_path(repo: Repo, ralph_dir: Path) -> None:
    state_path = ralph_dir / HOOKS_STATE_FILENAME
    if not state_path.exists():
        return

    lines = state_path.read_text().splitlines()
    if not lines:
        _unset_hooks_path(repo)
        state_path.unlink()
        return

    if lines[0] == "missing":
        _unset_hooks_path(repo)
    elif lines[0] == "value" and len(lines) > 1:
        _git_config(repo, "--local", "core.hooksPath", lines[1])

    state_path.unlink()


def _unset_hooks_path(repo: Repo) -> None:
    missing_key_status = 5
    try:
        _git_config(repo, "--local", "--unset-all", "core.hooksPath")
    except GitCommandError as exc:
        if exc.status == missing_key_status:
            return
        raise


def _remove_marker(ralph_dir: Path) -> None:
    path = ralph_dir / MARKER_FILENAME
    if path.exists():
        path.unlink()


def _remove_head_oid(ralph_dir: Path) -> None:
    path = ralph_dir / HEAD_OID_FILENAME
    if path.exists():
        path.unlink()


def _remove_track_file(ralph_dir: Path) -> None:
    path = ralph_dir / TRACK_FILENAME
    if path.exists():
        path.unlink()
