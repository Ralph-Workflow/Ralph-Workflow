"""Clone-layout regression coverage for automatic branch integration."""

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


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args), cwd=repo_root, capture_output=True, text=True, check=False, timeout=10
    )


def _commit(repo_root: Path) -> str:
    (repo_root / "feature.txt").write_text("feature\n", encoding="utf-8")
    assert _run(repo_root, "add", "feature.txt").returncode == 0
    assert _run(repo_root, "commit", "-m", "feature change").returncode == 0
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def test_auto_integrate_regression_remote_only_main_is_materialized_and_landed(
    tmp_git_repo: Path,
) -> None:
    """Plan step 2 / AC-03: clone-style origin/main lands without local main."""
    main = _run(tmp_git_repo, "branch", "--show-current").stdout.strip()
    bare = tmp_git_repo.parent / "origin.git"
    clone = tmp_git_repo.parent / "remote-only"
    assert _run(tmp_git_repo, "clone", "--bare", str(tmp_git_repo), str(bare)).returncode == 0
    clone.mkdir()
    assert _run(clone, "init").returncode == 0
    assert _run(clone, "config", "user.email", "test@example.com").returncode == 0
    assert _run(clone, "config", "user.name", "Test User").returncode == 0
    assert _run(clone, "remote", "add", "origin", str(bare)).returncode == 0
    assert _run(clone, "fetch", "origin", main).returncode == 0
    assert _run(clone, "checkout", "-b", "feature", f"origin/{main}").returncode == 0
    assert branch_sha(clone, main) is None

    feature_sha = _commit(clone)
    outcome = auto_integrate_after_commit(
        UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": True}}),
        WorkspaceScope(clone),
        RebaseState(),
    )

    assert outcome is not None
    assert outcome.last_action in {"rebased", "merged"}
    assert outcome.fast_forwarded is True
    assert branch_sha(clone, main) == feature_sha
