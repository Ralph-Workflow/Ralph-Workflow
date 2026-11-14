from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

__all__ = ["commit_file", "git"]

pytestmark = pytest.mark.subprocess_e2e


def git(root: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
        env={"HOME": str(root / ".scenario-home"), "PATH": "/usr/bin:/bin"},
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def commit_file(root: Path, relative: str, content: str, message: str) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git(root, "add", "--", relative)
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")
