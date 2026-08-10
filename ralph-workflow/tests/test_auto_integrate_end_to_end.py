"""Local-only target resolution, with one real-Git clone-layout proof."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.merge import branch_sha
from ralph.pipeline.auto_integrate import (
    auto_integrate_after_commit as _auto_integrate_after_commit,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

# ponytail: real Git clone and fetch setup under xdist can exceed 5s; the 60s suite budget remains authoritative.
pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def auto_integrate_after_commit(*args: Any, **kwargs: Any) -> Any:
    """Test-only wrapper that skips real CAS backoff sleeps."""
    kwargs.setdefault("sleep", lambda _seconds: None)
    kwargs.setdefault("jitter", lambda: 0.0)
    return _auto_integrate_after_commit(*args, **kwargs)


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
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


def test_remote_reconcile_conflict_regression_lands_feature_after_clean_abort(
    tmp_git_repo: Path,
) -> None:
    """S-2/S-3: an aborted target conflict cannot strand local feature landing."""
    main = _run(tmp_git_repo, "branch", "--show-current").stdout.strip()
    bare = tmp_git_repo.parent / "origin.git"
    writer = tmp_git_repo.parent / "writer"
    owner = tmp_git_repo.parent / "target-owner"

    assert _run(tmp_git_repo, "clone", "--bare", str(tmp_git_repo), str(bare)).returncode == 0
    assert _run(tmp_git_repo, "remote", "add", "origin", str(bare)).returncode == 0
    assert _run(tmp_git_repo, "push", "-u", "origin", main).returncode == 0
    (tmp_git_repo / "conflict.txt").write_text("local\n", encoding="utf-8")
    assert _run(tmp_git_repo, "add", "conflict.txt").returncode == 0
    assert _run(tmp_git_repo, "commit", "-m", "local target").returncode == 0
    assert _run(tmp_git_repo, "checkout", "-b", "feature").returncode == 0
    (tmp_git_repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    assert _run(tmp_git_repo, "add", "feature.txt").returncode == 0
    assert _run(tmp_git_repo, "commit", "-m", "feature").returncode == 0
    assert _run(tmp_git_repo, "worktree", "add", str(owner), main).returncode == 0

    assert _run(tmp_git_repo.parent, "clone", str(bare), str(writer)).returncode == 0
    assert _run(writer, "config", "user.name", "Test User").returncode == 0
    assert _run(writer, "config", "user.email", "test@example.com").returncode == 0
    (writer / "conflict.txt").write_text("remote\n", encoding="utf-8")
    assert _run(writer, "add", "conflict.txt").returncode == 0
    assert _run(writer, "commit", "-m", "remote target").returncode == 0
    assert _run(writer, "push", "origin", main).returncode == 0

    outcome = auto_integrate_after_commit(
        UnifiedConfig.model_validate(
            {
                "general": {
                    "auto_integrate_enabled": True,
                    "auto_integrate_target": main,
                    "auto_integrate_remote_enabled": True,
                    "auto_integrate_remote_interval_seconds": 0,
                }
            }
        ),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
    )

    assert outcome is not None
    assert outcome.freshness_safe is True
    assert branch_sha(owner, main) == branch_sha(tmp_git_repo, "feature")
    assert _run(owner, "status", "--porcelain").stdout == ""
