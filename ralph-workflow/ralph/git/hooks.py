"""Manage Ralph git hooks for the Python CLI."""

from __future__ import annotations

import os
import shutil
from typing import TYPE_CHECKING

from loguru import logger as default_logger

if TYPE_CHECKING:
    from pathlib import Path

    from loguru import Logger

import contextlib

from ralph.git.operations import find_repo_root

HOOK_MARKER = "RALPH_RUST_MANAGED_HOOK"
"""Marker string embedded in every Ralph-managed hook."""

RALPH_HOOK_NAMES = (
    "pre-commit",
    "pre-push",
    "pre-merge-commit",
    "commit-msg",
)
"""Hook names managed by Ralph workflows."""

_HOOK_DISPLAY_LABELS = {
    "pre-commit": "Commit",
    "pre-push": "Push",
    "pre-merge-commit": "Merge commit",
    "commit-msg": "Commit message",
}


def get_hooks_dir(repo_root: Path | str | None = None) -> Path:
    """Return the git hooks directory for a repository root."""

    repo_root_path = _resolve_repo_root(repo_root)
    hooks_dir = repo_root_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    return hooks_dir


def install_hooks_in_repo(repo_root: Path | str | None = None) -> Path:
    """Install every Ralph-managed git hook into ``repo_root``.

    The function walks :data:`RALPH_HOOK_NAMES`, ensures the repository's
    ``.git/hooks`` directory and the ``.git/ralph`` marker directory exist,
    and writes a generated hook script for each managed hook. The generated
    scripts first block any commit/push/merge when an agent-phase marker
    file is present, then delegate to the original hook script (backed up
    alongside as ``<hook_name>.ralph.orig``) when the marker is absent.

    The function is the writer half of the
    :func:`install_hooks_in_repo` / :func:`uninstall_hooks` pair; together
    they give Ralph a way to gate host-driven agent actions without
    permanently modifying the repository.

    Args:
        repo_root: Filesystem path or string path of the git repository
            root whose hooks should be installed. When ``None`` the
            repository is discovered via
            :func:`ralph.git.operations.find_repo_root` from the current
            working directory. A relative path is resolved as-is.

    Returns:
        Path: The absolute path to the repository's ``.git/hooks``
        directory, so callers can inspect the installed hooks or attach
        additional bookkeeping.

    Raises:
        OSError: If the host filesystem refuses to create the hook files
            (e.g. permission errors on a read-only mount). The error
            surfaces unchanged so callers can decide whether to abort the
            bootstrap or fall back to operator-supplied hooks.

    Side Effects:
        - Creates ``<repo>/.git/hooks`` and ``<repo>/.git/ralph`` if they
          do not already exist.
        - Touches the ``no_agent_commit`` and ``git-wrapper-dir.txt``
          marker files under ``<repo>/.git/ralph`` so subsequent agent
          phases can detect Ralph-managed installations.
        - Backs up any existing non-Ralph hook script as
          ``<hook_name>.ralph.orig`` before replacing it, so uninstall
          restores the user's original hook behavior.
        - Writes a Ralph-generated hook script for every name in
          :data:`RALPH_HOOK_NAMES`. The marker :data:`HOOK_MARKER` is
          embedded in each script so
          :func:`reinstall_hooks_if_tampered` can later detect drift.

    Trust Boundary:
        The function executes only filesystem writes inside the target
        repository's ``.git`` tree; it does not invoke any external
        commands or read user-supplied data. Callers must ensure
        ``repo_root`` resolves to a trusted repository before passing it
        in (a hostile caller could otherwise induce the script to write
        marker files in an attacker-chosen directory).
    """

    repo_root_path = _resolve_repo_root(repo_root)
    hooks_dir = get_hooks_dir(repo_root_path)
    ralph_dir = repo_root_path / ".git" / "ralph"
    ralph_dir.mkdir(parents=True, exist_ok=True)
    _ensure_marker_files(ralph_dir)

    for hook_name in RALPH_HOOK_NAMES:
        _install_hook(hook_name, hooks_dir, ralph_dir)

    return hooks_dir


def reinstall_hooks_if_tampered(
    *,
    logger: Logger | None = None,
    repo_root: Path | str | None = None,
) -> bool:
    """Reinstall hooks when they are missing or do not contain the marker."""

    logger = logger or default_logger
    repo_root_path = _resolve_repo_root(repo_root)
    hooks_dir = get_hooks_dir(repo_root_path)

    if _hooks_missing_or_tampered(hooks_dir):
        logger.warning("Git hooks tampered or missing — reinstalling Ralph hooks")
        install_hooks_in_repo(repo_root_path)
        return True

    return False


def uninstall_hooks(
    *,
    logger: Logger | None = None,
    repo_root: Path | str | None = None,
) -> bool:
    """Remove Ralph-managed hooks from ``repo_root`` and restore backups.

    Walks :data:`RALPH_HOOK_NAMES` and, for any hook that still carries the
    :data:`HOOK_MARKER` written by :func:`install_hooks_in_repo`, restores
    the original script from the ``<hook_name>.ralph.orig`` backup (when
    one exists) and removes the backup. When no backup exists the hook
    file is deleted outright, leaving the slot empty so subsequent
    ``pre-commit`` / ``pre-push`` invocations no longer fire at all.

    The function is the reverse half of the
    :func:`install_hooks_in_repo` / :func:`uninstall_hooks` pair. It only
    removes scripts that carry the Ralph marker, so it is safe to call on
    a repository whose hooks have already been replaced by another tool:
    foreign hooks are detected by the absence of the marker and left
    untouched.

    Keyword Args:
        logger: Optional :class:`loguru.Logger` instance used for the
            "Uninstalled N Ralph hook(s)" / "No Ralph-managed hooks were
            found to uninstall" summary lines. Defaults to the process
            logger when ``None``, which is the right choice for normal
            CLI invocations.
        repo_root: Filesystem path or string path of the git repository
            root whose hooks should be removed. When ``None`` the
            repository is discovered via
            :func:`ralph.git.operations.find_repo_root` from the current
            working directory.

    Returns:
        bool: ``True`` when at least one Ralph-managed hook was removed
        or restored; ``False`` when no managed hooks were found and the
        repository was already clean. The return value is intended for
        CLI-level reporting and is not load-bearing for the rest of the
        Ralph pipeline.

    Raises:
        OSError: If the host filesystem refuses to move or delete the
            hook files. The error surfaces unchanged so the caller can
            decide whether to retry with elevated permissions or surface
            the failure to the operator.

    Side Effects:
        - For every managed hook with a ``.ralph.orig`` backup, the
          backup is moved back into place as the active hook script and
          the backup file is removed.
        - For every managed hook without a backup, the hook file is
          deleted (leaving the slot empty rather than leaving a stub
          Ralph script).
        - Emits one ``logger.info`` line summarizing how many hooks were
          removed.

    Trust Boundary:
        The function only writes inside the target repository's ``.git``
        tree and never invokes external commands. Callers must ensure
        ``repo_root`` resolves to a trusted repository so that an attacker
        cannot trick the uninstall routine into deleting unrelated git
        hooks on a different host.
    """

    logger = logger or default_logger
    repo_root_path = _resolve_repo_root(repo_root)
    hooks_dir = get_hooks_dir(repo_root_path)

    removed = 0
    for hook_name in RALPH_HOOK_NAMES:
        removed += _remove_hook(hooks_dir / hook_name)

    if removed:
        logger.info("Uninstalled {removed} Ralph hook(s)", removed=removed)
    else:
        logger.info("No Ralph-managed hooks were found to uninstall")

    return bool(removed)


def _ensure_marker_files(ralph_dir: Path) -> None:
    for name in ("no_agent_commit", "git-wrapper-dir.txt"):
        (ralph_dir / name).touch(exist_ok=True)


def _install_hook(hook_name: str, hooks_dir: Path, ralph_dir: Path) -> None:
    hook_path = hooks_dir / hook_name
    _backup_existing_hook(hook_path)

    marker_path = ralph_dir / "no_agent_commit"
    track_file = ralph_dir / "git-wrapper-dir.txt"
    orig_path = _orig_hook_path(hooks_dir, hook_name)

    content = _make_hook_content(
        _hook_display_label(hook_name),
        marker_path,
        track_file,
        orig_path,
    )
    _write_hook_file(hook_path, content)


def _bash_single_quote(path: Path | str) -> str:
    value = str(path)
    return "'" + value.replace("'", "'\\''") + "'"


def _make_hook_content(
    hook_label: str,
    marker_path: Path,
    track_file_path: Path,
    orig_path: Path,
) -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"# {HOOK_MARKER} - generated by ralph\n\n"
        f"marker={_bash_single_quote(marker_path)}\n"
        f"track_file={_bash_single_quote(track_file_path)}\n\n"
        'if [[ -f "$marker" ]] || [[ -f "$track_file" ]]; then\n'
        f'  echo "{hook_label} blocked: agent phase protections active."\n'
        "  exit 1\n"
        "fi\n\n"
        f"orig={_bash_single_quote(orig_path)}\n"
        'if [[ -f "$orig" ]]; then\n'
        '  exec "$orig" "$@"\n'
        "fi\n\n"
        "exit 0\n"
    )


def _hook_display_label(hook_name: str) -> str:
    return _HOOK_DISPLAY_LABELS.get(hook_name, hook_name)


def _backup_existing_hook(hook_path: Path) -> None:
    if not hook_path.exists():
        return

    try:
        content = hook_path.read_text()
    except OSError:
        return

    if HOOK_MARKER in content:
        return

    orig_path = _orig_hook_path(hook_path.parent, hook_path.name)
    shutil.copyfile(hook_path, orig_path)
    _make_writable(orig_path)


def _orig_hook_path(hooks_dir: Path, hook_name: str) -> Path:
    return hooks_dir / f"{hook_name}.ralph.orig"


def _write_hook_file(hook_path: Path, content: str) -> None:
    if hook_path.exists():
        _make_writable(hook_path)
        with contextlib.suppress(OSError):
            hook_path.unlink()
    hook_path.write_text(content)
    _make_executable(hook_path)


def _make_executable(path: Path) -> None:
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        path.chmod(0o555)


def _make_writable(path: Path) -> None:
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        path.chmod(0o755)


def _hooks_missing_or_tampered(hooks_dir: Path) -> bool:
    return any(not _hook_has_marker(hooks_dir / name) for name in RALPH_HOOK_NAMES)


def _hook_has_marker(hook_path: Path) -> bool:
    if not hook_path.exists():
        return False
    try:
        return HOOK_MARKER in hook_path.read_text()
    except OSError:
        return False


def _remove_hook(hook_path: Path) -> int:
    if not _hook_has_marker(hook_path):
        return 0

    _make_writable(hook_path)
    orig_path = _orig_hook_path(hook_path.parent, hook_path.name)
    if orig_path.exists():
        shutil.move(orig_path, hook_path)
        _make_writable(hook_path)
        return 1

    with contextlib.suppress(FileNotFoundError):
        hook_path.unlink()
    return 1


def _resolve_repo_root(repo_root: Path | str | None) -> Path:
    if repo_root is None:
        return find_repo_root()
    return find_repo_root(repo_root)


__all__ = [
    "HOOK_MARKER",
    "RALPH_HOOK_NAMES",
    "get_hooks_dir",
    "install_hooks",
    "install_hooks_in_repo",
    "reinstall_hooks_if_tampered",
    "uninstall_hooks",
]

install_hooks = install_hooks_in_repo
