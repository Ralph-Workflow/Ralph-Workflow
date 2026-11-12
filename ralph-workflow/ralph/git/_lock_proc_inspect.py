from __future__ import annotations

import errno
import functools
import os
import shutil
import time
from contextlib import suppress
from pathlib import Path
from typing import Literal

_UNPROVABLE_LOCK_CEILING_S = 3600.0
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


def _git_binary_identity_uncached() -> tuple[int, int] | None:
    git_binary = shutil.which("git")
    if git_binary is None:
        return None
    try:
        st = Path(git_binary).stat()
        return (st.st_dev, st.st_ino)
    except OSError:
        return None


_git_binary_identity = functools.lru_cache(maxsize=1)(_git_binary_identity_uncached)


def _can_rule_out_non_git_process(proc_root: Path, pid_str: str) -> bool:
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
    git_ident = _git_binary_identity()
    if git_ident is not None:
        with suppress(OSError):
            exe_stat = (proc_root / pid_str / "exe").stat()
            if (exe_stat.st_dev, exe_stat.st_ino) == git_ident:
                return False
    return bool(names)


def _cmdline_targets_repo(
    proc_root: Path, pid_str: str, gitdir: bytes | Path, gitdir_common: bytes | Path
) -> bool:
    try:
        raw = (proc_root / pid_str / "cmdline").read_bytes()
    except OSError:
        return False
    gb = gitdir if isinstance(gitdir, bytes) else os.fsencode(gitdir)
    gcb = gitdir_common if isinstance(gitdir_common, bytes) else os.fsencode(gitdir_common)
    if b".git" not in raw and gb not in raw and gcb not in raw:
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
    try:
        target = str(fd_link.readlink())
    except OSError:
        return False
    if target == lock_resolved:
        return True
    if lock_ident is None:
        return False
    if not (target.endswith(".lock") or ".lock" in target):
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
    pid_dir = proc_root / pid_str

    is_zombie, uids = _read_proc_status(pid_dir)
    if is_zombie:
        return "dead"

    fd_status = _pid_holds_lock_fd(pid_dir, lock_resolved, lock_ident)
    if fd_status == "live":
        return "live"
    if fd_status == "unknown":
        lock_age_s = time.time() - lock_mtime if lock_mtime is not None else None
        return _rule_out_unreadable(proc_root, pid_str, uids, lock_uid, lock_age_s)
    is_non_git = _can_rule_out_non_git_process(proc_root, pid_str)
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
    if not os.access(proc_root, os.R_OK):
        return []
    try:
        return [int(entry.name) for entry in proc_root.iterdir() if entry.name.isdigit()]
    except OSError:
        return []
