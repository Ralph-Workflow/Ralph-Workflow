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
    """State carrier for one agent-phase git-protection sequence.

    :class:`GitHelpers` is the lightweight bundle :func:`start_agent_phase`
    and :func:`end_agent_phase` populate as they enable and later roll back
    the agent-phase git protections. The two functions use it to share a
    view of the runtime state (which directory is the Ralph-managed hooks
    dir, which repository root the protections belong to, and which ``git``
    binary is the genuine system one) without recomputing it on every
    call.

    Attributes:
        real_git: Path to the genuine ``git`` executable on the host. Set
            by callers when the Ralph wrapper redirects ``git`` to a
            sandbox binary; ``None`` when the host has no wrapper in front
            of ``git``. The wrapper scripts use this to invoke the real
            binary once the agent-phase protections are restored.
        wrapper_dir: Path to the per-repository ``.git/ralph`` directory
            that holds the agent-phase marker, HEAD OID snapshot, and the
            Ralph-managed hooks directory. Set by :func:`start_agent_phase`
            and read by :func:`end_agent_phase` during teardown. ``None``
            before :func:`start_agent_phase` populates it.
        wrapper_repo_root: Path to the repository root the protections
            were enabled for. ``None`` before :func:`start_agent_phase`
            populates it; :func:`end_agent_phase` asserts the value
            matches the repo being torn down so a mismatched call fails
            fast instead of editing the wrong repository's ``.git``.

    Lifecycle:
        1. Construct (or reuse) a :class:`GitHelpers`. Optional — the
           phase helpers create one for you.
        2. Call :func:`start_agent_phase(repo_root, helpers)`; it sets
           ``wrapper_repo_root`` and ``wrapper_dir`` and writes the
           marker / HEAD OID / hooks-path snapshot.
        3. Run the agent phase (commit/push attempts are blocked by the
           marker file the hook scripts check).
        4. Call :func:`end_agent_phase(repo_root, helpers)`; it restores
           ``core.hooksPath``, deletes the marker / snapshot / track
           files, and leaves ``wrapper_dir`` populated only until the next
           start/end cycle.

    Invariants:
        - The class is a plain data carrier with no locking; threads that
          enable protections concurrently must serialize around the
          instance externally.
        - All attributes are intentionally typed ``Path | None`` because
          none of them carry meaning outside of a paired
          ``start_agent_phase`` / ``end_agent_phase`` cycle.
    """

    real_git: Path | None
    wrapper_dir: Path | None
    wrapper_repo_root: Path | None

    def __init__(self) -> None:
        self.real_git = None
        self.wrapper_dir = None
        self.wrapper_repo_root = None


def start_agent_phase(repo_root: Path | str, helpers: GitHelpers | None = None) -> None:
    """Enable git protections for an agent phase.

    Installs the Ralph-managed git hooks that block agent commits for the
    remainder of the current phase. The function writes a marker file and
    the current ``HEAD`` OID under ``<repo>/.git/ralph/``, snapshots the
    previous ``core.hooksPath`` value, and repoints ``core.hooksPath`` at
    the Ralph-managed hooks directory. Subsequent agent invocations that
    attempt to commit or push are rejected by the hooks.

    This function is the public entry point for enabling the protection
    scheme and is paired with :func:`end_agent_phase`, which rolls back
    the changes. Together they bracket one agent phase; callers that
    skip :func:`end_agent_phase` leave the repository in a state that
    still blocks commits.

    Args:
        repo_root: Path to the repository root whose protections should be
            enabled. Accepts a :class:`pathlib.Path` or a string; the
            value is resolved against the GitPython ``Repo`` constructor.
        helpers: Optional pre-built :class:`GitHelpers` carrier. When
            ``None``, a fresh carrier is constructed and populated with
            ``wrapper_repo_root`` and ``wrapper_dir``. The same carrier
            (or one with identical population) must be passed to the
            matching :func:`end_agent_phase` call so teardown targets the
            right repository.

    Returns:
        None. The function mutates the repository's ``.git/ralph``
        directory, the local ``core.hooksPath`` config, and the supplied
        :class:`GitHelpers` carrier.

    Side effects:
        - Creates ``<repo>/.git/ralph/`` if absent.
        - Writes the marker, HEAD OID, and track files inside it.
        - Snapshots and overwrites the local ``core.hooksPath``.
        - Closes the GitPython ``Repo`` it opened for the duration of the
          call (callers must not reuse the original handle).

    Raises:
        git.exc.GitCommandError: If any underlying ``git`` invocation
            fails (filesystem permission, missing ``.git``, or a held
            lock). Each subprocess is bounded by
            ``GIT_SUBPROCESS_TIMEOUT_SECONDS`` so a stuck lock cannot
            hang an agent-phase setup.

    See also:
        :func:`end_agent_phase` rolls back the protections this
        function installs. :func:`detect_unauthorized_commit` reports
        whether ``HEAD`` advanced during a protected phase.
    """

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
    """Remove agent-phase protections and restore git state.

    Reverses every change made by :func:`start_agent_phase`: restores the
    previous ``core.hooksPath`` value, deletes the Ralph-managed marker /
    HEAD-OID / track files under ``<repo>/.git/ralph/``, and closes the
    GitPython ``Repo`` opened for the duration of the call. After this
    function returns, the repository is in the same git state it was in
    before the matching :func:`start_agent_phase` call.

    Args:
        repo_root: Path to the repository root whose protections should
            be rolled back. Must match the ``repo_root`` passed to the
            matching :func:`start_agent_phase` call so teardown targets
            the same repository. Accepts a :class:`pathlib.Path` or a
            string.
        helpers: Optional :class:`GitHelpers` carrier populated by the
            matching :func:`start_agent_phase` call. When ``None``, a
            fresh carrier is constructed and ``wrapper_repo_root`` is
            set to ``Path(repo_root)`` so teardown can locate the
            ``.git/ralph`` directory written during setup.

    Returns:
        None. The function mutates the repository's local
        ``core.hooksPath`` and deletes the marker / snapshot files
        written during setup.

    Side effects:
        - Restores the previous ``core.hooksPath`` from the snapshot
          file (or clears it if none was set).
        - Deletes the marker, HEAD OID, and track files inside
          ``<repo>/.git/ralph/``.
        - Closes the GitPython ``Repo`` it opened for the duration of
          the call.

    Raises:
        git.exc.GitCommandError: If any underlying ``git`` invocation
            fails (filesystem permission, missing snapshot file, or a
            held lock). Each subprocess is bounded by
            ``GIT_SUBPROCESS_TIMEOUT_SECONDS`` so a stuck lock cannot
            hang an agent-phase teardown.

    See also:
        :func:`start_agent_phase` installs the protections this
        function rolls back. :func:`detect_unauthorized_commit` reports
        whether ``HEAD`` advanced during a protected phase, which this
        function neither inspects nor clears.
    """
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
    """Return True if the HEAD OID no longer matches the stored baseline.

    Compares the repository's current ``HEAD`` against the OID
    :func:`start_agent_phase` snapshotted into
    ``<repo>/.git/ralph/head-oid.txt``. A mismatch indicates that an
    agent phase wrote a commit despite the protection hooks — a
    condition the supervisor should treat as a security violation
    and surface to the user before any further work continues.

    The function is read-only: it does not modify the repository,
    delete the snapshot file, or invoke the hooks. Callers that want
    a single boolean answer for a check-and-act flow should call
    this function and decide the policy themselves; a follow-up
    :func:`end_agent_phase` will still roll back the protections
    regardless of the return value.

    Args:
        repo_root: Path to the repository root to inspect. Accepts a
            :class:`pathlib.Path` or a string; the value is resolved
            against the GitPython ``Repo`` constructor.

    Returns:
        bool: ``True`` when a stored snapshot exists and the current
        ``HEAD`` OID differs from it (unauthorized commit detected);
        ``False`` when no snapshot exists, the snapshot is empty,
        the current ``HEAD`` cannot be read, or ``HEAD`` still
        matches the snapshot.

    Side effects:
        - Closes the GitPython ``Repo`` it opened for the duration of
          the call.
        - Does NOT mutate any file under ``<repo>/.git/ralph/`` and
          does NOT invoke the hooks scripts.

    Raises:
        git.exc.GitCommandError: Re-raised only when the underlying
            ``git`` invocation fails for a reason other than a missing
            ``HEAD`` (detached/unborn HEAD is reported as ``False``,
            not raised).

    See also:
        :func:`start_agent_phase` writes the snapshot this function
        compares against. :func:`end_agent_phase` removes it.
    """

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
