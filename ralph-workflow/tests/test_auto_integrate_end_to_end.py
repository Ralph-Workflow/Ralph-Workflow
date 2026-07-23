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


def _build_remote_only_clone(tmp_git_repo: Path, name: str) -> tuple[Path, str]:
    """Build a clone whose mainline exists ONLY as ``refs/remotes/origin/<main>``.

    Returns ``(clone_root, main_branch_name)``. The clone is checked out
    on ``feature`` and has no local mainline branch at all.
    """
    main = _run(tmp_git_repo, "branch", "--show-current").stdout.strip()
    bare = tmp_git_repo.parent / f"{name}.git"
    clone = tmp_git_repo.parent / name
    assert _run(tmp_git_repo, "clone", "--bare", str(tmp_git_repo), str(bare)).returncode == 0
    clone.mkdir()
    assert _run(clone, "init").returncode == 0
    assert _run(clone, "config", "user.email", "test@example.com").returncode == 0
    assert _run(clone, "config", "user.name", "Test User").returncode == 0
    assert _run(clone, "remote", "add", "origin", str(bare)).returncode == 0
    assert _run(clone, "fetch", "origin", main).returncode == 0
    assert _run(clone, "checkout", "-b", "feature", f"origin/{main}").returncode == 0
    assert branch_sha(clone, main) is None
    return clone, main


def test_remote_only_main_is_never_materialized_into_a_local_branch(
    tmp_git_repo: Path,
) -> None:
    """A mainline that exists only on origin must stay on origin.

    Target resolution is a stateless, local-refs-only check: a
    remote-tracking ref must never be turned into a local branch, or
    remote state would decide the base of the very first local rebase.
    With no local candidate the step records a skip and mutates nothing.
    """
    clone, main = _build_remote_only_clone(tmp_git_repo, "remote-only")

    _commit(clone)
    outcome = auto_integrate_after_commit(
        UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": True}}),
        WorkspaceScope(clone),
        RebaseState(),
    )

    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert branch_sha(clone, main) is None, (
        "a local branch was created from a remote-tracking ref"
    )


def test_configured_target_missing_locally_records_a_skip(
    tmp_git_repo: Path,
) -> None:
    """An explicitly PINNED target follows the same local-only rule.

    A configured ``auto_integrate_target`` that does not exist as a
    local branch is a recorded skip -- it is never created from
    ``refs/remotes/origin/<target>``, exactly like the auto-detect path.
    """
    clone, main = _build_remote_only_clone(tmp_git_repo, "configured-target")

    _commit(clone)
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
    assert branch_sha(clone, main) is None, (
        "a local branch was created from a remote-tracking ref"
    )
