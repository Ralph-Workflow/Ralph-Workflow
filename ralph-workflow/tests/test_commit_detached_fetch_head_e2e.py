from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from ralph.git.errors import GitOperationError
from ralph.git.operations import create_commit, get_head_sha

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]

_IDENTITY = ("-c", "user.name=t", "-c", "user.email=t@t")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=False, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_a_checkout_detached_at_fetch_head_is_never_committed_onto_the_pseudoref(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, *_IDENTITY, "commit", "-q", "--allow-empty", "-m", "base")
    _git(tmp_path, "fetch", "-q", ".", "main")
    _git(tmp_path, "checkout", "-q", "--detach", "FETCH_HEAD")
    _git(tmp_path, *_IDENTITY, "commit", "-q", "--allow-empty", "-m", "detached work")
    detached_tip = get_head_sha(tmp_path)
    (tmp_path / "change.txt").write_text("staged\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")

    with pytest.raises(GitOperationError, match="no feature branch to commit onto") as raised:
        create_commit(tmp_path, "feat: on a detached checkout", expected_head=detached_tip)

    assert "FETCH_HEAD" not in str(raised.value)
    assert detached_tip in str(raised.value)
    assert get_head_sha(tmp_path) == detached_tip
    assert _git(tmp_path, "diff", "--cached", "--name-only") == "change.txt"


def test_a_detached_checkout_whose_branch_moved_on_is_not_reset_onto_that_branch(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, *_IDENTITY, "commit", "-q", "--allow-empty", "-m", "base")
    _git(tmp_path, "checkout", "-q", "--detach")
    _git(tmp_path, *_IDENTITY, "commit", "-q", "--allow-empty", "-m", "detached work")
    detached_tip = get_head_sha(tmp_path)

    with pytest.raises(GitOperationError, match="no feature branch to commit onto"):
        create_commit(tmp_path, "feat: on a detached checkout", expected_head=detached_tip)

    assert get_head_sha(tmp_path) == detached_tip


def test_a_detached_checkout_at_a_branch_tip_commits_onto_that_branch(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, *_IDENTITY, "commit", "-q", "--allow-empty", "-m", "base")
    base = get_head_sha(tmp_path)
    _git(tmp_path, "checkout", "-q", "--detach")
    (tmp_path / "change.txt").write_text("staged\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")

    create_commit(tmp_path, "feat: on a detached branch tip", expected_head=base)

    new_tip = _git(tmp_path, "rev-parse", "refs/heads/main")
    assert new_tip != base
    assert _git(tmp_path, "rev-parse", f"{new_tip}^") == base
    assert _git(tmp_path, "symbolic-ref", "HEAD") == "refs/heads/main"
    assert get_head_sha(tmp_path) == new_tip
    assert not (tmp_path / ".git" / "main").exists()
    assert _git(tmp_path, "for-each-ref", "--format=%(refname)") == "refs/heads/main"
