"""Git wrapper helpers for blocking commits during agent phases."""

from __future__ import annotations

from pathlib import Path

from git import GitCommandError, Repo

from ralph.timeout_defaults import GIT_SUBPROCESS_TIMEOUT_SECONDS

#: Bound each GitPython subprocess (``repo.git.*``) so a held .git lock or a
#: pathological filesystem cannot wedge an agent-phase setup/teardown forever.
#: GitPython kills the git child after this many seconds.
_GIT_OP_TIMEOUT_SECONDS = GIT_SUBPROCESS_TIMEOUT_SECONDS

MARKER_FILENAME = "no_agent_commit"
TRACK_FILENAME = "git-wrapper-dir.txt"
HEAD_OID_FILENAME = "head-oid.txt"
HOOKS_STATE_FILENAME = "hooks-path-state"
HOOKS_DIR_NAME = "hooks"


class GitHelpers:
    """Simple placeholder for Git wrapper state tracking."""

    real_git: Path | None
    wrapper_dir: Path | None
    wrapper_repo_root: Path | None

    def __init__(self) -> None:
        self.real_git = None
        self.wrapper_dir = None
        self.wrapper_repo_root = None


def start_agent_phase(repo_root: Path | str, helpers: GitHelpers | None = None) -> None:
    """Enable git protections for an agent phase."""

    repo = Repo(repo_root)
    helpers = helpers or GitHelpers()
    helpers.wrapper_repo_root = Path(repo_root)
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
    """Remove agent-phase protections and restore git state."""
    repo = Repo(repo_root)
    helpers = helpers or GitHelpers()
    helpers.wrapper_repo_root = Path(repo_root)
    try:
        ralph_dir = _ralph_dir_from_repo(repo)
        _restore_hooks_path(repo, ralph_dir)
        _remove_marker(ralph_dir)
        _remove_head_oid(ralph_dir)
        _remove_track_file(ralph_dir)
    finally:
        repo.close()


def detect_unauthorized_commit(repo_root: Path | str) -> bool:
    """Return True if the HEAD OID no longer matches the stored baseline."""

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
    (ralph_dir / MARKER_FILENAME).write_text("")


def _write_track_file(ralph_dir: Path) -> None:
    (ralph_dir / TRACK_FILENAME).write_text(str(ralph_dir))


def _capture_head_oid(repo: Repo, ralph_dir: Path) -> None:
    try:
        oid = repo.head.commit.hexsha
    except (ValueError, GitCommandError):
        return

    (ralph_dir / HEAD_OID_FILENAME).write_text(f"{oid}\n")


def _set_hooks_path(repo: Repo, ralph_dir: Path) -> None:
    hooks_dir = ralph_dir / HOOKS_DIR_NAME
    hooks_dir.mkdir(parents=True, exist_ok=True)
    repo.git.config(
        "--local", "core.hooksPath", str(hooks_dir), kill_after_timeout=_GIT_OP_TIMEOUT_SECONDS
    )


def _read_hooks_path(repo: Repo) -> str | None:
    try:
        value = repo.git.config(
            "--local", "--get", "core.hooksPath", kill_after_timeout=_GIT_OP_TIMEOUT_SECONDS
        )
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
        state_path.write_text("missing\n")
    else:
        state_path.write_text(f"value\n{hooks_value}\n")


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
        repo.git.config(
            "--local", "core.hooksPath", lines[1], kill_after_timeout=_GIT_OP_TIMEOUT_SECONDS
        )

    state_path.unlink()


def _unset_hooks_path(repo: Repo) -> None:
    missing_key_status = 5
    try:
        repo.git.config(
            "--local", "--unset-all", "core.hooksPath", kill_after_timeout=_GIT_OP_TIMEOUT_SECONDS
        )
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
