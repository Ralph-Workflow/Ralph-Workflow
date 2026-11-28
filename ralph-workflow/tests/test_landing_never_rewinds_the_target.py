from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pytest

from ralph.pipeline.integration_ladder import default_git_plumbing

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(30),
]


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    completed = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _commit(cwd: Path, name: str) -> str:
    (cwd / name).write_text(f"{name}\n")
    _git(cwd, "add", ".")
    _git(cwd, "commit", "-q", "-m", name)
    return _git(cwd, "rev-parse", "HEAD")


def test_a_feature_built_on_an_older_target_does_not_replace_the_target(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "base.txt")
    feature = tmp_path / "feature"
    _git(repo, "worktree", "add", "-q", "-b", "feature", str(feature), "main")
    _commit(feature, "feature.txt")
    sibling_landing = _commit(repo, "sibling.txt")

    result = default_git_plumbing().land_in_worktree(
        feature, feature, "main", expected_sha=sibling_landing
    )

    assert result != "landed"
    assert _git(repo, "rev-parse", "refs/heads/main") == sibling_landing
