"""Real-git regression for the production integration-ladder git adapter.

Pins the two production behaviors the fakes cannot prove against the real
binary:

* the ``changed_paths`` porcelain probe exits 0 with the config flags
  BEFORE the subcommand — the ``-c``-after-subcommand ordering made git
  exit 129, which the adapter used to read as "clean" — and a dirty tree
  is actually detected as non-empty paths;
* the compare-and-swap land moves ``refs/heads/<target>`` only from the
  expected old value (``update-ref --no-deref <ref> <new> <old>``,
  re-verified) and a concurrent advance is named ``target-moved`` with no
  overwrite.

Few seconds of real git at most: one init, two commits, one worktree add.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ralph.pipeline.integration_ladder import default_git_plumbing

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(30),
]


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    completed = subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    )
    return completed.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    (main / "base.txt").write_text("base\n")
    _git(main, "add", ".")
    _git(main, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    return main


def test_changed_paths_detects_dirt_and_the_config_first_probe_exits_zero(
    repo: Path,
) -> None:
    (repo / "dirty.txt").write_text("dirty\n")
    plumbing = default_git_plumbing()

    paths = plumbing.changed_paths(repo)

    # A non-None answer proves the probe exited 0: with the config flag
    # after the subcommand git exits 129 and the adapter returns None.
    assert paths is not None
    assert "dirty.txt" in paths


def test_land_compares_and_swaps_and_a_stale_expectation_overwrites_nothing(
    repo: Path, tmp_path: Path
) -> None:
    # The original checkout does its own work on a feature branch, as the
    # ladder's root worktree does; landing ``main`` must not move it.
    _git(repo, "checkout", "-q", "-b", "feature")
    observed = _git(repo, "rev-parse", "HEAD").strip()
    work = tmp_path / "work"
    _git(repo, "worktree", "add", "-q", "--detach", str(work), "main")
    (work / "feat.txt").write_text("feat\n")
    _git(work, "add", ".")
    _git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "feat")
    tip = _git(work, "rev-parse", "HEAD").strip()
    plumbing = default_git_plumbing()

    assert plumbing.land_in_worktree(repo, work, "main", expected_sha=observed) == "landed"
    assert _git(repo, "rev-parse", "refs/heads/main").strip() == tip

    # The target has moved past the stale expectation: named race, no overwrite.
    assert plumbing.land_in_worktree(repo, work, "main", expected_sha=observed) == "target-moved"
    assert _git(repo, "rev-parse", "refs/heads/main").strip() == tip

    # The original checkout is still where the ladder observed it: the
    # safe repoint resets it onto the landed tip.
    assert plumbing.repoint_original(repo, work, observed_sha=observed) == "repointed"
    assert _git(repo, "rev-parse", "HEAD").strip() == tip
