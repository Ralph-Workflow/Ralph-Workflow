from __future__ import annotations

import atexit
import io
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

GIT_TIMEOUT_SECONDS = 60

_TEMP_ROOTS: list[Path] = []


def _cleanup_temp_roots() -> None:
    while _TEMP_ROOTS:
        shutil.rmtree(_TEMP_ROOTS.pop(), ignore_errors=True)


def repo_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
    )


def head_tree(package_root: Path) -> Path | None:
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
