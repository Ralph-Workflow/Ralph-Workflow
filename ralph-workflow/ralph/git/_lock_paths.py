"""Git lock path validation and discovery for the stale-lock recovery gate.

Every unlink of a ``*.lock`` file starts here. The path is parsed out of
git's stderr, checked against the reapability gate (a real git lock inside a
real git dir, never a symlink, never a Ralph-owned lock) and resolved to the
git dir that owns it. This module also owns the pure discovery helpers that
enumerate the lock files a checkout could have orphaned and the git dirs a
checkout lives in.

Split out of ``ralph.git._lock_recovery`` to stay within the repo-structure
line limits; the public surface is unchanged and re-exported from there.
"""

from __future__ import annotations

import os
import re
from contextlib import suppress
from contextvars import ContextVar
from pathlib import Path

_LOCK_PATH_PATTERN = re.compile(r"Unable to create '(.+?\.lock)'", re.IGNORECASE)

#: Lock names git itself uses at the top of a git dir. They are accepted by name
#: (the path in an error message may be relative or reached through a symlink);
#: any other ``*.lock`` must be located inside a recognisable git dir.
_KNOWN_GIT_LOCK_NAMES = frozenset(
    {
        "index.lock",
        "HEAD.lock",
        "packed-refs.lock",
        "config.lock",
        "shallow.lock",
        "ORIG_HEAD.lock",
        "MERGE_HEAD.lock",
        "FETCH_HEAD.lock",
    }
)
_CONFIG_LOCK_PATTERN = re.compile(r"could not lock config file (.+?): File exists", re.IGNORECASE)
_GIT_DIR_MARKERS = ("refs", "logs", "objects", "modules")


#: Directories under a git dir whose ``*.lock`` files are git's (ref, reflog,
#: submodule and linked-worktree locks).
_STRUCTURAL_LOCK_DIRS = frozenset({"refs", "logs", "modules", "worktrees"})
_lock_base_dir: ContextVar[Path | None] = ContextVar("ralph_git_lock_base_dir", default=None)


def _is_inside_git_dir(path: Path) -> bool:
    """Return True when ``path`` lives in a git directory (checkout, worktree or bare).

    A lock is only ever removed from a real git dir, never from an arbitrary
    path that happens to appear in an error message.
    """
    if ".git" in path.parts:
        return True
    for ancestor in list(path.parents)[:6]:
        if (ancestor / "HEAD").is_file() and (
            (ancestor / "objects").is_dir() or (ancestor / "commondir").is_file()
        ):
            return True
    return False


def _is_ralph_lock(path: Path) -> bool:
    """Ralph's own lock files (lease, auto-integrate) are never git locks."""
    return (
        path.name.startswith("ralph")
        or path.name == "agent-lease.lock"
        or path.parent.name == "ralph"
    )


def _lock_path_is_reapable(lock_path: Path) -> bool:
    """Gate every unlink: the path must be a real git lock inside a real git dir.

    The path can originate from git's stderr, which can carry repository- or
    hook-controlled text, so it is never trusted on its own: no relative paths,
    no ``..``, no symlinks (the file or any parent), no Ralph-owned locks, and
    only git's own lock names or ``*.lock`` in git's ref / reflog / submodule /
    worktree directories.
    """
    well_formed = (
        lock_path.is_absolute()
        and ".." not in lock_path.parts
        and lock_path.name.endswith(".lock")
        and not _is_ralph_lock(lock_path)
    )
    if not well_formed:
        return False
    try:
        if lock_path.is_symlink():
            return False
        real = Path(os.path.realpath(lock_path.parent)) / lock_path.name
    except OSError:
        return False
    # Every name -- git's own top-level ones included -- must sit in a real git
    # dir: the path comes from stderr, and a file called ``index.lock`` somewhere
    # else on disk is not ours to remove.
    if not _is_inside_git_dir(real):
        return False
    if lock_path.name in _KNOWN_GIT_LOCK_NAMES:
        return True
    return bool(_STRUCTURAL_LOCK_DIRS & set(real.parts))


def _parse_lock_path_from_error(error_text: str, base_dir: Path | None = None) -> Path | None:
    match = _LOCK_PATH_PATTERN.search(error_text)
    if match is not None:
        candidate = Path(match.group(1))
    else:
        config_match = _CONFIG_LOCK_PATTERN.search(error_text)
        if config_match is None:
            return None
        config_path: str = config_match[1]
        candidate = Path(config_path.strip().strip("'\"") + ".lock")
    if not candidate.is_absolute():
        base = base_dir if base_dir is not None else _lock_base_dir.get()
        if base is None:
            return None
        candidate = base / candidate
    return candidate if _lock_path_is_reapable(candidate) else None


def _gitdir_for_lock(lock_path: Path) -> Path:
    """Return the git dir (private for a linked worktree) that owns ``lock_path``."""
    parts = lock_path.parts
    if ".git" in parts:
        dot_git = len(parts) - 1 - parts[::-1].index(".git")
        # ``.git/worktrees/<name>/...`` is that worktree's private git dir.
        if parts[dot_git + 1 : dot_git + 2] == ("worktrees",) and len(parts) > dot_git + 3:
            return Path(*parts[: dot_git + 3])
        return Path(*parts[: dot_git + 1])
    marker_indexes = [parts.index(m) for m in _GIT_DIR_MARKERS if m in parts]
    if not marker_indexes:
        return lock_path.parent
    return Path(*parts[: min(marker_indexes)])


def names_a_lock_file(error_text: str) -> bool:
    """Return True when git's message names a specific lock file that blocked it.

    Stricter than :func:`is_lock_failure`: "cannot lock ref" alone also reports a
    permanent ref-name conflict, which no amount of waiting resolves.
    """
    return bool(_LOCK_PATH_PATTERN.search(error_text) or _CONFIG_LOCK_PATTERN.search(error_text))


def _walk_locks(base: Path) -> list[Path]:
    """Return ``*.lock`` files under ``base`` without ever following a symlink."""
    found: list[Path] = []
    if base.is_symlink() or not base.is_dir():
        return found
    for (
        dirpath,
        dirnames,
        filenames,
    ) in os.walk(  # filesystem-read-ok: enumerate git lock files under gitdir without following symlinks
        base, followlinks=False, onerror=lambda _e: None
    ):
        here = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not (here / d).is_symlink()]
        found.extend(here / f for f in filenames if f.endswith(".lock"))
    return found


def git_lock_candidates(git_dir: Path, common_dir: Path | None = None) -> list[Path]:
    """Return every lock file git itself could have orphaned for this checkout.

    Covers the private (per-worktree) git dir and the common dir: git's named
    top-level locks, ref and reflog locks, linked-worktree and submodule git
    dirs. Never follows a symlink, and Ralph's own lock files are never
    candidates.
    """
    roots = [git_dir] if common_dir is None or common_dir == git_dir else [git_dir, common_dir]
    found: list[Path] = []
    for root in roots:
        if root.is_symlink() or not root.is_dir():
            continue
        found.extend(
            root / name for name in sorted(_KNOWN_GIT_LOCK_NAMES) if (root / name).exists()
        )
        for sub in ("refs", "logs", "modules", "worktrees"):
            found.extend(_walk_locks(root / sub))
    seen: set[Path] = set()
    candidates: list[Path] = []
    for path in found:
        if path not in seen and _lock_path_is_reapable(path):
            seen.add(path)
            candidates.append(path)
    return candidates


def git_dirs_from_git_dir(git_dir: Path) -> tuple[Path, Path | None] | None:
    """Return ``(git_dir, common_dir)`` for a path that IS a git dir, else None.

    A GitPython ``Repo.git_dir`` already names the (private) git dir, so no
    walk is needed; a duck-typed stand-in that merely carries a repo root does
    not look like one (no ``HEAD``) and is left alone.
    """
    if not (git_dir / "HEAD").is_file():
        return None
    commondir = git_dir / "commondir"
    common: Path | None = None
    if commondir.is_file():
        with suppress(OSError):
            common = (git_dir / commondir.read_text(encoding="utf-8").strip()).resolve()
    return git_dir, common


def find_git_dirs(cwd: Path) -> tuple[Path, Path | None] | None:
    """Return ``(git_dir, common_dir)`` for the checkout containing ``cwd`` using only the filesystem.

    Handles a normal checkout (``.git`` directory) and a linked worktree
    (``.git`` file pointing at ``<common>/worktrees/<name>``). No git process is
    spawned, so it is cheap enough to run before every git command.
    """
    for directory in (cwd, *cwd.parents):
        dot_git = directory / ".git"
        if dot_git.is_dir():
            return dot_git, None
        if dot_git.is_file():
            try:
                text = dot_git.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if not text.startswith("gitdir:"):
                return None
            private = Path(text.split(":", 1)[1].strip())
            if not private.is_absolute():
                private = (directory / private).resolve()
            commondir = private / "commondir"
            common: Path | None = None
            if commondir.is_file():
                with suppress(OSError):
                    common = (private / commondir.read_text(encoding="utf-8").strip()).resolve()
            return private, common
    return None


#: Substrings in git's stderr that mean "a lock file blocked me". Some messages
#: name no lock path at all ("Unable to write index"), so recovery then looks
#: at the repository's lock files instead of parsing one out.
_LOCK_FAILURE_MARKERS = (
    ".lock': file exists",
    "cannot lock ref",
    "could not lock config file",
    "unable to write index",
    "could not write index",
    "unable to lock",
    "could not lock",
)


def is_lock_failure(stderr: str) -> bool:
    """Return True when git's stderr says a lock file blocked the command."""
    lowered = stderr.lower()
    return any(marker in lowered for marker in _LOCK_FAILURE_MARKERS)
