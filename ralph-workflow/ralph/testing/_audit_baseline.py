"""Committed-baseline helpers for the diff-based audit gates.

The gates themselves never shell out to version control — they take their
baseline as an input so they stay deterministic and unit-testable with
synthetic input. This module is the seam the coordinator owns: it
materialises the committed ``HEAD`` copy of the package and the
working-tree diff, so a gate can be wired into ``make verify`` and still
report only what the change adds or touches.

The helpers degrade to "no baseline" when the package is not inside a git
checkout (an installed wheel, for instance). A gate that needs a baseline
then reports what it can see rather than inventing one.
"""

from __future__ import annotations

import atexit
import io
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

#: The git sub-command timeout for every baseline query.
GIT_TIMEOUT_SECONDS = 60

#: Temporary directories created for a materialised baseline. They are
#: removed when the interpreter exits.
_TEMP_ROOTS: list[Path] = []


def _cleanup_temp_roots() -> None:
    """Remove every temporary baseline tree this process created."""
    while _TEMP_ROOTS:
        shutil.rmtree(_TEMP_ROOTS.pop(), ignore_errors=True)


def repo_root(start: Path) -> Path | None:
    """Return the git checkout that owns *start*, or None."""
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    """Run a read-only git command inside *cwd*."""
    return subprocess.run(  # resource-lifecycle-ok: read-only git command for diff baseline with bounded timeout  # filesystem-poll-ok: read-only git baseline query, not a managed child process or polling loop
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
    )


def head_tree(package_root: Path) -> Path | None:
    """Materialise the committed ``HEAD`` copy of the package.

    Returns the package directory inside a temporary tree, or None when the
    package is not inside a git checkout or git could not produce the
    archive.
    """
    checkout = repo_root(package_root)
    if checkout is None:
        return None
    package_name = package_root.name
    archive = _run_git(["archive", "--format=tar", "HEAD", package_name], checkout)
    if archive.returncode != 0:
        return None
    destination = Path(tempfile.mkdtemp(prefix="ralph-head-baseline-"))
    _TEMP_ROOTS.append(destination)
    atexit.register(_cleanup_temp_roots)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as archive_file:
        archive_file.extractall(destination, filter="data")
    return destination / package_name


def worktree_diff(package_root: Path) -> str:
    """Return the working-tree diff against ``HEAD``, package-relative."""
    checkout = repo_root(package_root)
    if checkout is None:
        return ""
    diff = _run_git(
        ["diff", "HEAD", "--relative", "--", package_root.name],
        checkout,
    )
    if diff.returncode != 0:
        return ""
    return diff.stdout.decode("utf-8", "replace")
