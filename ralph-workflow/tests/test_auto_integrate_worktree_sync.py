"""Minimal real-worktree coverage for automatic branch integration.

Equivalent happy-path, untracked-file, prefix-collision, conflict fallback,
declined-resolution, and retry-policy cases were deleted from this file because
their behavior is already pinned at faster public seams elsewhere in the suite.
These tests retain only interactions that require Git's linked-worktree state:
landing through a checked-out dirty target and convergence across two agents.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.merge import branch_sha
from ralph.pipeline.auto_integrate import (
    auto_integrate_after_commit,
    auto_integrate_on_phase_transition,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(20)]


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def _base_branch(repo_root: Path) -> str:
    return _run(repo_root, "symbolic-ref", "--quiet", "HEAD").stdout.strip().removeprefix(
        "refs/heads/"
    )


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    path = repo_root / filename
    path.write_text(content, encoding="utf-8")
    assert _run(repo_root, "add", filename).returncode == 0
    assert _run(repo_root, "commit", "-m", message).returncode == 0
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _config(target: str) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_target": target}}
    )


def _add_worktree(repo_root: Path, path: Path, branch: str) -> None:
    assert _run(repo_root, "worktree", "add", "-b", branch, str(path)).returncode == 0


def test_dirty_checked_out_target_lands_without_losing_operator_changes(
    tmp_git_repo: Path,
) -> None:
    """Git must update the target worktree and preserve an unrelated dirty file."""
    main = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "tracked.txt", "base\n", "seed tracked file")
    feature = tmp_git_repo.parent / "feature-ff"
    _add_worktree(tmp_git_repo, feature, "feature-ff")
    _commit(feature, "feature.txt", "feature\n", "feature change")
    dirty_file = tmp_git_repo / "tracked.txt"
    dirty_file.write_text("operator work\n", encoding="utf-8")

    outcome = auto_integrate_after_commit(
        _config(main), WorkspaceScope(feature), RebaseState()
    )

    assert outcome is not None
    assert outcome.fast_forwarded is True
    assert branch_sha(tmp_git_repo, main) == _run(feature, "rev-parse", "HEAD").stdout.strip()
    assert dirty_file.read_text(encoding="utf-8") == "operator work\n"
    assert (tmp_git_repo / "feature.txt").exists()
    status = _run(
        tmp_git_repo, "status", "--porcelain", "--untracked-files=no"
    ).stdout
    assert "tracked.txt" in status
    assert "feature.txt" not in status


def test_two_linked_agents_converge_at_the_phase_boundary(
    tmp_git_repo: Path,
) -> None:
    """A previously landed agent catches up to a sibling through shared refs."""
    main = _base_branch(tmp_git_repo)
    feature_a = tmp_git_repo.parent / "feature-a"
    feature_b = tmp_git_repo.parent / "feature-b"
    _add_worktree(tmp_git_repo, feature_a, "feature-a")
    _add_worktree(tmp_git_repo, feature_b, "feature-b")

    _commit(feature_a, "a.txt", "a\n", "feature a")
    first = auto_integrate_after_commit(
        _config(main), WorkspaceScope(feature_a), RebaseState()
    )
    _commit(feature_b, "b.txt", "b\n", "feature b")
    second = auto_integrate_after_commit(
        _config(main), WorkspaceScope(feature_b), RebaseState()
    )
    catch_up = auto_integrate_on_phase_transition(
        _config(main), WorkspaceScope(feature_a), RebaseState()
    )

    assert first is not None and first.fast_forwarded is True
    assert second is not None and second.fast_forwarded is True
    assert catch_up is not None and catch_up.fast_forwarded is True
    final_main = branch_sha(tmp_git_repo, main)
    assert _run(feature_a, "rev-parse", "HEAD").stdout.strip() == final_main
    assert _run(feature_b, "rev-parse", "HEAD").stdout.strip() == final_main
    assert (feature_a / "b.txt").exists()
    assert (feature_b / "a.txt").exists()
