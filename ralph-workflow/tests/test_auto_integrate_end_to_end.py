"""Local-only target resolution, with one real-Git clone-layout proof."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.merge import branch_sha
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def test_remote_only_target_is_not_materialized_locally(
    tmp_git_repo: Path,
) -> None:
    """A remote-tracking ref cannot become the first local integration base."""
    main = _run(tmp_git_repo, "branch", "--show-current").stdout.strip()
    bare = tmp_git_repo.parent / "origin.git"
    clone = tmp_git_repo.parent / "agent"
    assert _run(tmp_git_repo, "clone", "--bare", str(tmp_git_repo), str(bare)).returncode == 0
    clone.mkdir()
    assert _run(clone, "init").returncode == 0
    assert _run(clone, "remote", "add", "origin", str(bare)).returncode == 0
    assert _run(clone, "fetch", "origin", main).returncode == 0
    assert _run(clone, "checkout", "-b", "feature", f"origin/{main}").returncode == 0
    assert branch_sha(clone, main) is None

    outcome = auto_integrate_after_commit(
        UnifiedConfig.model_validate(
            {
                "general": {
                    "auto_integrate_enabled": True,
                    "auto_integrate_target": main,
                }
            }
        ),
        WorkspaceScope(clone),
        RebaseState(),
    )

    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert branch_sha(clone, main) is None
