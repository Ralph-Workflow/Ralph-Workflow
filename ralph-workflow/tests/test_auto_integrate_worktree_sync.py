"""Minimal real-worktree coverage for automatic branch integration.

Equivalent happy-path, untracked-file, prefix-collision, conflict fallback,
declined-resolution, and retry-policy cases were deleted from this file because
their behavior is already pinned at faster public seams elsewhere in the suite.
This test retains only the interaction that requires Git's linked-worktree
state: landing through a checked-out dirty target. Cross-agent convergence is
covered through the injected phase-boundary seam and the dedicated catch-up
proofs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.merge import branch_sha
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
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
