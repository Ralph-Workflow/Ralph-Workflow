"""Minimal real-Git proof for exact linked-worktree branch matching.

The former full integration scenario duplicated broader landing coverage in
``test_auto_integrate_worktree_sync.py``. This focused test retains only the
Git worktree-list parsing behavior that an in-memory seam cannot prove.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.git.rebase.rebase_preconditions import check_rebase_preconditions

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def test_prefix_extended_sibling_branch_does_not_block_rebase(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    """A sibling on ``wt-040-fix`` must not be mistaken for ``wt-040``."""
    sibling = tmp_path / "sibling"
    assert _run(
        tmp_git_repo,
        "worktree",
        "add",
        "-b",
        "wt-040-fix",
        str(sibling),
    ).returncode == 0
    assert _run(tmp_git_repo, "checkout", "-b", "wt-040").returncode == 0

    check_rebase_preconditions(tmp_git_repo)
