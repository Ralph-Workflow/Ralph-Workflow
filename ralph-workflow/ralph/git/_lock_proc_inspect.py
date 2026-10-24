"""Per-process inspection primitives for the git lock liveness probe.

Every function here answers ONE question about ONE process: does this PID
hold the lock, can it be ruled out as a holder, or is it unknowable? The
aggregation of those per-process verdicts into a single lock verdict lives in
:mod:`ralph.git._lock_liveness`.

Split out of ``ralph.git._lock_recovery`` to stay within the repo-structure
line limits; the public surface is unchanged and re-exported from there.
"""

from __future__ import annotations

import errno
import os
import shutil
import time
from contextlib import suppress
from pathlib import Path
from typing import Literal

#: A lock that no inspectable process holds, but that an uninspectable process
#: could still hold, is reaped once it is this old. No git operation holds
#: ``index.lock`` or a ref lock for an hour; the pipeline waits and retries
#: until then, so an orphan is always eventually recoverable while a lock that
#: might be live is never removed early.
_UNPROVABLE_LOCK_CEILING_S = 3600.0
#: Infrastructure daemons that cannot be git clients. When their fd table is
#: unreadable (they run with elevated capabilities, so they are non-dumpable)
#: they are ruled out by name instead of blocking recovery on every host.
_INFRASTRUCTURE_DAEMONS = (
    "systemd",
    "(sd-pam)",
    "sshd",
    "ssh-agent",
    "gpg-agent",
    "dbus-",
    "login",
    "cron",
    "agetty",
    "polkitd",
    "udisksd",
    "pipewire",
    "pulseaudio",
    "wireplumber",
    "gdm",
    "gnome-",
    "xdg-",
    "at-spi",
)


def _is_git_process(proc_root: Path, pid_str: str) -> bool:
    """Return True if the process identified by pid_str is a git process.

    Checks ``/proc/<pid>/comm`` and ``/proc/<pid>/cmdline``. If either source
    indicates the executable name is or contains "git", returns True.
    Raises PermissionError if inspection is denied by permissions.
    """
    comm_path = proc_root / pid_str / "comm"
    try:
        if comm_path.is_file():
            comm = comm_path.read_text(encoding="utf-8", errors="replace").strip().lower()
            if "git" in comm:
                return True
    except PermissionError:
        raise
    except OSError:
        pass

    cmdline_path = proc_root / pid_str / "cmdline"
    try:
        if cmdline_path.is_file():
            cmdline = cmdline_path.read_bytes()
            parts = [p.decode(errors="replace") for p in cmdline.split(b"\x00") if p]
            if parts:
                exe = Path(parts[0]).name.lower()
                if "git" in exe:
                    return True
            elif b"git" in cmdline.lower():
                return True
    except PermissionError:
        raise
    except OSError:
        pass

    return False


def _can_rule_out_non_git_process(proc_root: Path, pid_str: str) -> bool:
    """Return True if we can positively prove the process is NOT a git process.

    Every available name must agree: ``comm`` and argv[0] are attacker/wrapper
    controlled (``ln -s /usr/bin/git g``), so the executable's own name from
    ``/proc/<pid>/exe`` is consulted whenever it is readable.
    """
    names: list[str] = []
    try:
        comm = (proc_root / pid_str / "comm").read_text(encoding="utf-8", errors="replace")
        if comm.strip():
            names.append(comm.strip().lower())
    except OSError:
        pass
    try:
        raw = (proc_root / pid_str / "cmdline").read_bytes()
        parts = [p.decode(errors="replace") for p in raw.split(b"\x00") if p]
        if parts:
            names.append(Path(parts[0]).name.lower())
    except OSError:
        pass
    with suppress(OSError):
        names.append((proc_root / pid_str / "exe").readlink().name.lower())
    if any("git" in name for name in names):
        return False
    # A copy or link of the git binary under another name is still git.
    git_binary = shutil.which("git")
    if git_binary is not None:
        with suppress(OSError):
            git_stat = Path(git_binary).stat()
            exe_stat = (proc_root / pid_str / "exe").stat()
            if (git_stat.st_dev, git_stat.st_ino) == (exe_stat.st_dev, exe_stat.st_ino):
                return False
    return bool(names)


def _cmdline_targets_repo(
    proc_root: Path, pid_str: str, gitdir: bytes | Path, gitdir_common: bytes | Path
) -> bool:
    """Return True when the process's argv names this repository's git dir.

    ``git --git-dir=<repo>/.git ...`` run from an unrelated cwd works on the
    repository without a matching cwd or ``GIT_DIR`` in its environment. Paths
    are resolved against the process's own cwd and symlinks, so a relative
    ``--git-dir=../r/.git`` or a symlinked path still matches.
    """
    try:
        raw = (proc_root / pid_str / "cmdline").read_bytes()
    except OSError:
        return False
    targets = {Path(os.fsdecode(g)).resolve() for g in (gitdir, gitdir_common)}
    try:
        base = (proc_root / pid_str / "cwd").readlink()
    except OSError:
        base = None
    for arg in (a.decode(errors="replace") for a in raw.split(b"\x00") if a):
        value = arg.split("=", 1)[1] if arg.startswith("--") and "=" in arg else arg
        candidate = Path(value)
        if not candidate.is_absolute():
            if base is None:
                continue
            candidate = base / candidate
        with suppress(OSError):
            resolved = candidate.resolve()
            if any(resolved == t or t in resolved.parents for t in targets):
                return True
    return False


def _is_unreadable_os_error(e: OSError) -> bool:
    return isinstance(e, PermissionError) or e.errno in (errno.EACCES, errno.EPERM)


def _is_dead_process_os_error(e: OSError) -> bool:
    return isinstance(e, (FileNotFoundError, ProcessLookupError)) or e.errno in (
        errno.ENOENT,
        errno.ESRCH,
    )


def _fd_is_lock(fd_link: Path, lock_resolved: str, lock_ident: tuple[int, int] | None) -> bool:
    """Return True when ``fd_link`` is the lock file, by path OR by identity.

    The path comparison alone misses a holder that sees the repository under a
    different path (bind mount, container, symlinked checkout) or whose link
    carries a `` (deleted)`` suffix. ``stat`` follows the magic link to the
    open file itself, so ``(st_dev, st_ino)`` is the authoritative match.
    """
    if str(fd_link.readlink()) == lock_resolved:
        return True
    if lock_ident is None:
        return False
    try:
        st = fd_link.stat()
    except OSError:
        return False
    return (st.st_dev, st.st_ino) == lock_ident


def _pid_holds_lock_fd(
    pid_dir: Path,
    lock_resolved: str,
    lock_ident: tuple[int, int] | None = None,
) -> Literal["live", "not_live", "unknown"]:
    """Check if process has the lock file open in fd/."""
    try:
        for fd_link in (pid_dir / "fd").iterdir():
            try:
                if _fd_is_lock(fd_link, lock_resolved, lock_ident):
                    return "live"
            except OSError as e:
                if _is_unreadable_os_error(e):
                    return "unknown"
        return "not_live"
    except OSError as e:
        if _is_unreadable_os_error(e):
            return "unknown"
        if _is_dead_process_os_error(e):
            return "not_live"
        return "unknown"


def _pid_in_repo_cwd(
    pid_dir: Path,
    live_cwds: set[Path],
    common_worktrees_dir: Path,
) -> Literal["in_repo", "not_in_repo", "unknown"]:
    """Check if process cwd is in the workspace repository or gitdirs.

    A process is treated as "in the repo" when its cwd is ANY path inside the
    repository: the worktree-private gitdir, the common gitdir, the worktree
    working tree, the main-repo working tree, any subdirectory of any of those,
    or any path under ``gitdir_common/worktrees/``. This covers the common case
    of a developer running ``git status`` from a project subdirectory while a
    long-running ``git gc`` holds a worktree-private lock.
    """
    try:
        cwd_link = pid_dir / "cwd"
        if not (cwd_link.is_symlink() or cwd_link.exists()):
            return "not_in_repo"
        cwd_target = cwd_link.readlink()
        try:
            cwd_resolved = cwd_target.resolve()
        except OSError:
            cwd_resolved = cwd_target
        if (
            bool({cwd_target, cwd_resolved} & (live_cwds | {common_worktrees_dir}))
            or str(cwd_target).startswith(str(common_worktrees_dir) + "/")
            or str(cwd_resolved).startswith(str(common_worktrees_dir) + "/")
            or any(
                str(cwd_resolved).startswith(str(cwd) + "/")
                or str(cwd_target).startswith(str(cwd) + "/")
                for cwd in (live_cwds | {common_worktrees_dir})
                if cwd != common_worktrees_dir
            )
        ):
            return "in_repo"
        return "not_in_repo"
    except OSError as e:
        if _is_unreadable_os_error(e):
            return "unknown"
        if _is_dead_process_os_error(e):
            return "not_in_repo"
        return "unknown"


def _resolve_environ_gitdir(pid_dir: Path, raw_val: bytes) -> tuple[Path | None, bool]:
    """Resolve raw GIT_DIR bytes against cwd; returns (resolved_path, resolution_failed)."""
    if not raw_val:
        return None, False
    try:
        val_str = os.fsdecode(raw_val)
        raw_path = Path(val_str)
        if raw_path.is_absolute():
            return raw_path.resolve(), False
        proc_cwd = (pid_dir / "cwd").readlink()
        if not proc_cwd.is_absolute():
            return None, True
        return (proc_cwd / raw_path).resolve(), False
    except (OSError, UnicodeDecodeError):
        return None, True


def _pid_has_gitdir_environ(
    pid_dir: Path,
    gitdir: bytes | Path,
    gitdir_common: bytes | Path,
) -> Literal["matches", "no_match", "unknown"]:
    """Check if process environ sets GIT_DIR matching the repository gitdir.

    Parses exact NUL-separated GIT_DIR=... entries from /proc/<pid>/environ.
    Relative GIT_DIR paths are resolved against the inspected process's cwd
    (/proc/<pid>/cwd via readlink). If resolution cannot be established,
    fails closed to 'unknown'.
    """
    try:
        environ_path = pid_dir / "environ"
        if not environ_path.is_file():
            return "no_match"
        environ_bytes = environ_path.read_bytes()
    except OSError as e:
        if _is_dead_process_os_error(e):
            return "no_match"
        return "unknown"

    try:
        targets = {
            Path(os.fsdecode(g) if isinstance(g, (bytes, bytearray)) else g).resolve()
            for g in (gitdir, gitdir_common)
        }
    except OSError:
        return "unknown"

    resolution_failed = False
    for entry in environ_bytes.split(b"\x00"):
        if entry.startswith(b"GIT_DIR="):
            candidate, failed = _resolve_environ_gitdir(pid_dir, entry[8:])
            if failed:
                resolution_failed = True
            elif candidate is not None and candidate in targets:
                return "matches"

    return "unknown" if resolution_failed else "no_match"


#: Git subcommands that only read. They never take ``index.lock`` or a ref
#: lock, so a long-lived one (a persistent ``cat-file --batch`` helper that
#: GitPython or an IDE keeps in the repository cwd) is not evidence of a holder.
_READ_ONLY_GIT_SUBCOMMANDS = frozenset(
    {
        "cat-file",
        "rev-parse",
        "rev-list",
        "for-each-ref",
        "ls-tree",
        "show-ref",
        "merge-base",
        "name-rev",
        "check-ignore",
        "check-attr",
        "diff-tree",
        "var",
    }
)
_GIT_OPTS_WITH_ARG = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})


def _git_subcommand(proc_root: Path, pid_str: str) -> str | None:
    """Return the git subcommand from ``/proc/<pid>/cmdline``, if parseable."""
    try:
        raw = (proc_root / pid_str / "cmdline").read_bytes()
    except OSError:
        return None
    args = [a.decode(errors="replace") for a in raw.split(b"\x00") if a]
    skip_next = False
    for arg in args[1:]:
        if skip_next:
            skip_next = False
        elif arg in _GIT_OPTS_WITH_ARG:
            skip_next = True
        elif not arg.startswith("-"):
            return arg
    return None


def _read_proc_status(pid_dir: Path) -> tuple[bool, tuple[int, ...] | None]:
    """Return ``(is_zombie, uids)`` from one read of ``/proc/<pid>/status``.

    ``status`` is world-readable, unlike ``fd/`` and ``environ``, so it is the
    one source that classifies a process we are not allowed to inspect.
    ``uids`` is the real, effective, saved and filesystem uid.
    """
    is_zombie = False
    uids: tuple[int, ...] | None = None
    try:
        for line in (pid_dir / "status").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("State:"):
                state = line.split(":", 1)[1].strip()
                is_zombie = state.startswith(("Z", "X")) or "zombie" in state.lower()
            elif line.startswith("Uid:"):
                fields = line.split(":", 1)[1].split()
                if fields and all(f.isdigit() for f in fields):
                    uids = tuple(int(f) for f in fields)
    except OSError:
        pass
    return is_zombie, uids


def _check_git_liveness(
    proc_root: Path,
    pid_str: str,
    pid_dir: Path,
    live_cwds: set[Path],
    common_worktrees_dir: Path,
    gitdir: bytes | Path,
    gitdir_common: bytes | Path,
) -> Literal["live", "dead", "unknown"]:
    """Resolve liveness for a git process whose fd table was readable and lacks the lock.

    Git can hold a lock without an open fd for an instant (a ref transaction
    between prepare and commit), so a read-WRITE git working inside the
    repository is still treated as a holder. Read-only plumbing is not.
    """
    if _git_subcommand(proc_root, pid_str) in _READ_ONLY_GIT_SUBCOMMANDS:
        return "dead"
    if _cmdline_targets_repo(proc_root, pid_str, gitdir, gitdir_common):
        return "live"
    cwd_status = _pid_in_repo_cwd(pid_dir, live_cwds, common_worktrees_dir)
    env_status = _pid_has_gitdir_environ(pid_dir, gitdir, gitdir_common)

    if cwd_status == "unknown" or env_status == "unknown":
        return "unknown"

    if cwd_status == "in_repo" or env_status == "matches":
        try:
            if _is_git_process(proc_root, pid_str):
                return "live"
        except PermissionError:
            return "unknown"

    return "dead"


def _is_infrastructure_daemon(proc_root: Path, pid_str: str) -> bool:
    try:
        comm = (proc_root / pid_str / "comm").read_text(encoding="utf-8").strip().lower()
    except OSError:
        return False
    return comm.startswith(_INFRASTRUCTURE_DAEMONS)


def _rule_out_unreadable(
    proc_root: Path,
    pid_str: str,
    uids: tuple[int, ...] | None,
    lock_uid: int | None,
    lock_age_s: float | None,
) -> Literal["dead", "unknown"]:
    """Decide a process whose fd table we may not read, using only sound facts.

    ``dead`` requires an affirmative reason this process cannot hold the lock:
    none of its uids is the lock owner's (git creates the lock with its own
    uid), it is an infrastructure daemon that is not a git client, or the lock
    has outlived any operation that could legitimately hold it
    (``_UNPROVABLE_LOCK_CEILING_S``). Everything else stays ``unknown`` and fails
    closed. A name such as "python" or an executable merely not called git is
    NOT a rule-out: a libgit2/dulwich host can hold the lock, and a start time
    versus mtime comparison is not trusted either (mtime is mutable).
    """
    if lock_uid is not None and uids is not None and lock_uid not in uids:
        return "dead"
    if _is_infrastructure_daemon(proc_root, pid_str):
        return "dead"
    if lock_age_s is not None and lock_age_s > _UNPROVABLE_LOCK_CEILING_S:
        return "dead"
    return "unknown"


def _non_git_touches_repo(
    proc_root: Path,
    pid_str: str,
    pid_dir: Path,
    live_cwds: set[Path],
    common_worktrees_dir: Path,
    gitdir: bytes | Path,
    gitdir_common: bytes | Path,
) -> bool:
    """Return True when a non-git process works in, or names, this repository.

    GitPython's ``LockFile`` creates the lock, closes the fd, and holds it by
    presence alone, so "no open fd" does not clear a process that is operating
    on the repository. Ralph's own process is excluded: it is the one asking.
    """
    if pid_str == str(os.getpid()):
        return False
    return _pid_in_repo_cwd(
        pid_dir, live_cwds, common_worktrees_dir
    ) != "not_in_repo" or _cmdline_targets_repo(proc_root, pid_str, gitdir, gitdir_common)


def _check_pid_liveness(
    proc_root: Path,
    pid_str: str,
    lock_resolved: str,
    live_cwds: set[Path],
    common_worktrees_dir: Path,
    gitdir: bytes | Path,
    gitdir_common: bytes | Path,
    lock_uid: int | None = None,
    lock_ident: tuple[int, int] | None = None,
    lock_mtime: float | None = None,
) -> Literal["live", "dead", "unknown"]:
    """Check liveness signals for a single process PID.

    An open fd on the lock (by path or identity) is the authoritative positive
    proof and always wins. Only when the fd table cannot be read do the
    uid / start-time / non-git rule-outs apply; only when it can be read and
    lacks the lock does the git cwd/environ heuristic apply.

    Returns:
        "live" -- process holds the lock open, or is a read-write git process
                  working inside the repository.
        "dead" -- process cannot be holding the lock.
        "unknown" -- process cannot be inspected and cannot be ruled out.
    """
    pid_dir = proc_root / pid_str

    is_zombie, uids = _read_proc_status(pid_dir)
    if is_zombie:
        return "dead"

    fd_status = _pid_holds_lock_fd(pid_dir, lock_resolved, lock_ident)
    if fd_status == "live":
        return "live"
    is_non_git = _can_rule_out_non_git_process(proc_root, pid_str)
    if fd_status == "unknown":
        lock_age_s = time.time() - lock_mtime if lock_mtime is not None else None
        return _rule_out_unreadable(proc_root, pid_str, uids, lock_uid, lock_age_s)
    if is_non_git and not _non_git_touches_repo(
        proc_root, pid_str, pid_dir, live_cwds, common_worktrees_dir, gitdir, gitdir_common
    ):
        return "dead"
    return _check_git_liveness(
        proc_root,
        pid_str,
        pid_dir,
        live_cwds,
        common_worktrees_dir,
        gitdir,
        gitdir_common,
    )


def _list_pids(proc_root: Path) -> list[int]:
    """Return the numeric entries of ``proc_root``; empty when it cannot be listed."""
    if not os.access(proc_root, os.R_OK):
        return []
    try:
        return [int(entry.name) for entry in proc_root.iterdir() if entry.name.isdigit()]
    except OSError:
        return []
