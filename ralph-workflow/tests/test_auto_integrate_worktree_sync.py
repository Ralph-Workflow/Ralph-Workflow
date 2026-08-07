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

import importlib
import os
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

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(20)]


def auto_integrate_after_commit(*args: Any, **kwargs: Any) -> Any:
    """Test-only wrapper that skips real CAS backoff sleeps."""
    kwargs.setdefault("sleep", lambda _seconds: None)
    kwargs.setdefault("jitter", lambda: 0.0)
    return _auto_integrate_after_commit(*args, **kwargs)


def _run(
    repo_root: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
        env=None if env is None else {**os.environ, **env},
    )


def _base_branch(repo_root: Path) -> str:
    return (
        _run(repo_root, "symbolic-ref", "--quiet", "HEAD")
        .stdout.strip()
        .removeprefix("refs/heads/")
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


def test_reclaim_regression_snapshot_failure_restores_the_original_index(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2/AC-7: a failed snapshot leaves tracked, staged, and untracked state intact."""
    main = _base_branch(tmp_git_repo)
    tracked = tmp_git_repo / "tracked.txt"
    _commit(tmp_git_repo, tracked.name, "base\n", "seed tracked file")
    tracked.write_text("unstaged\n", encoding="utf-8")
    assert _run(tmp_git_repo, "add", tracked.name).returncode == 0
    tracked.write_text("staged plus unstaged\n", encoding="utf-8")
    untracked = tmp_git_repo / "untracked.txt"
    untracked.write_text("untracked\n", encoding="utf-8")
    before_status = _run(tmp_git_repo, "status", "--porcelain=v1", "-z").stdout
    before_cached = _run(tmp_git_repo, "diff", "--cached", "--binary").stdout
    reclaim_module = importlib.import_module("ralph.pipeline._auto_integrate_reclaim")
    original_run_git = reclaim_module.run_git

    def fail_commit(args: tuple[str, ...], **kwargs: object) -> object:
        if kwargs.get("label") == "auto-integrate:reclaim-commit":
            return subprocess.CompletedProcess(args, 1, "", "injected failure")
        return original_run_git(args, **kwargs)

    monkeypatch.setattr(reclaim_module, "run_git", fail_commit)

    assert reclaim_module.reclaim_dirty_target_worktree(tmp_git_repo, main) is None
    assert _run(tmp_git_repo, "status", "--porcelain=v1", "-z").stdout == before_status
    assert _run(tmp_git_repo, "diff", "--cached", "--binary").stdout == before_cached
    assert tracked.read_text(encoding="utf-8") == "staged plus unstaged\n"
    assert untracked.read_text(encoding="utf-8") == "untracked\n"


def test_reclaim_regression_stage_failure_restores_the_original_index(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2/AC-7: a failed staging snapshot leaves every owner byte intact."""
    main = _base_branch(tmp_git_repo)
    tracked = tmp_git_repo / "tracked.txt"
    _commit(tmp_git_repo, tracked.name, "base\n", "seed tracked file")
    tracked.write_text("staged\n", encoding="utf-8")
    assert _run(tmp_git_repo, "add", tracked.name).returncode == 0
    tracked.write_text("staged plus unstaged\n", encoding="utf-8")
    untracked = tmp_git_repo / "untracked.txt"
    untracked.write_text("untracked\n", encoding="utf-8")
    before_status = _run(tmp_git_repo, "status", "--porcelain=v1", "-z").stdout
    before_cached = _run(tmp_git_repo, "diff", "--cached", "--binary").stdout
    reclaim_module = importlib.import_module("ralph.pipeline._auto_integrate_reclaim")
    original_run_git = reclaim_module.run_git

    def fail_stage(args: tuple[str, ...], **kwargs: object) -> object:
        result = original_run_git(args, **kwargs)
        if kwargs.get("label") == "auto-integrate:reclaim-stage":
            return subprocess.CompletedProcess(args, 1, "", "injected failure after staging")
        return result

    monkeypatch.setattr(reclaim_module, "run_git", fail_stage)

    assert reclaim_module.reclaim_dirty_target_worktree(tmp_git_repo, main) is None
    assert _run(tmp_git_repo, "status", "--porcelain=v1", "-z").stdout == before_status
    assert _run(tmp_git_repo, "diff", "--cached", "--binary").stdout == before_cached
    assert tracked.read_text(encoding="utf-8") == "staged plus unstaged\n"
    assert untracked.read_text(encoding="utf-8") == "untracked\n"


def test_reclaim_refuses_a_feature_worktree(
    tmp_git_repo: Path,
) -> None:
    """AC-8: reclamation can discard only the worktree owning the target."""
    main = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "tracked.txt", "base\n", "seed tracked file")
    feature = tmp_git_repo.parent / "feature-owner"
    _add_worktree(tmp_git_repo, feature, "feature-owner")
    dirty = feature / "feature.txt"
    dirty.write_text("must survive\n", encoding="utf-8")

    reclaim_module = importlib.import_module("ralph.pipeline._auto_integrate_reclaim")

    assert reclaim_module.reclaim_dirty_target_worktree(feature, main) is None
    assert dirty.read_text(encoding="utf-8") == "must survive\n"


def test_reclaim_regression_prunes_expired_and_excess_snapshot_refs(
    tmp_git_repo: Path,
) -> None:
    """DA-001: reclaim snapshots retain only recent refs within the per-target cap."""
    main = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "tracked.txt", "base\n", "seed tracked file")
    reclaim_module = importlib.import_module("ralph.pipeline._auto_integrate_reclaim")
    prefix = f"refs/ralph-reclaim/{main}/"
    old_ref = f"{prefix}old"
    other_ref = "refs/ralph-reclaim/other-target/keep"
    tree = _run(tmp_git_repo, "write-tree").stdout.strip()
    old_commit = _run(
        tmp_git_repo,
        "commit-tree",
        tree,
        "-p",
        "HEAD",
        "-m",
        "old snapshot",
        env={"GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z"},
    ).stdout.strip()
    assert _run(tmp_git_repo, "update-ref", old_ref, old_commit).returncode == 0
    assert _run(tmp_git_repo, "update-ref", other_ref, "HEAD").returncode == 0
    for index in range(reclaim_module._RECLAIM_REF_MAX_COUNT):
        ref = f"{prefix}recent-{index}"
        assert _run(tmp_git_repo, "update-ref", ref, "HEAD").returncode == 0

    dirty = tmp_git_repo / "tracked.txt"
    dirty.write_text("operator work\n", encoding="utf-8")

    snapshot_ref = reclaim_module.reclaim_dirty_target_worktree(tmp_git_repo, main)

    assert snapshot_ref is not None
    refs = _run(tmp_git_repo, "for-each-ref", "--format=%(refname)", prefix).stdout.splitlines()
    assert snapshot_ref in refs
    assert old_ref not in refs
    assert len(refs) <= reclaim_module._RECLAIM_REF_MAX_COUNT
    assert other_ref in _run(
        tmp_git_repo, "for-each-ref", "--format=%(refname)", "refs/ralph-reclaim/other-target/"
    ).stdout.splitlines()


def test_dirty_checked_out_target_snapshots_then_lands(
    tmp_git_repo: Path,
) -> None:
    """S-3: dirty target ownership is snapshotted then reclaimed before landing."""
    main = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "tracked.txt", "base\n", "seed tracked file")
    feature = tmp_git_repo.parent / "feature-ff"
    _add_worktree(tmp_git_repo, feature, "feature-ff")
    _commit(feature, "feature.txt", "feature\n", "feature change")
    dirty_file = tmp_git_repo / "feature.txt"
    dirty_file.write_text("operator work\n", encoding="utf-8")

    outcome = auto_integrate_after_commit(_config(main), WorkspaceScope(feature), RebaseState())

    assert outcome is not None
    assert outcome.fast_forwarded is True
    assert branch_sha(tmp_git_repo, main) == _run(feature, "rev-parse", "HEAD").stdout.strip()
    assert dirty_file.read_text(encoding="utf-8") == "feature\n"
    assert (tmp_git_repo / "feature.txt").exists()
    assert _run(tmp_git_repo, "status", "--porcelain").stdout == ""
    snapshots = _run(tmp_git_repo, "for-each-ref", "--format=%(refname)", "refs/ralph-reclaim").stdout
    assert f"refs/ralph-reclaim/{main}/" in snapshots


def test_reclaim_regression_discard_failure_restores_owner_state(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2/E15: a failed clean rolls the target owner back byte-for-byte."""
    main = _base_branch(tmp_git_repo)
    tracked = tmp_git_repo / "tracked.txt"
    _commit(tmp_git_repo, tracked.name, "base\n", "seed tracked file")
    tracked.write_text("staged\n", encoding="utf-8")
    assert _run(tmp_git_repo, "add", tracked.name).returncode == 0
    tracked.write_text("staged plus unstaged\n", encoding="utf-8")
    untracked = tmp_git_repo / "untracked.txt"
    untracked.write_text("untracked\n", encoding="utf-8")
    before_status = _run(tmp_git_repo, "status", "--porcelain=v1", "-z").stdout
    before_cached = _run(tmp_git_repo, "diff", "--cached", "--binary").stdout
    reclaim_module = importlib.import_module("ralph.pipeline._auto_integrate_reclaim")
    original_run_git = reclaim_module.run_git

    def fail_clean(args: tuple[str, ...], **kwargs: object) -> object:
        if kwargs.get("label") == "auto-integrate:reclaim-clean":
            return subprocess.CompletedProcess(args, 1, "", "injected failure")
        return original_run_git(args, **kwargs)

    monkeypatch.setattr(reclaim_module, "run_git", fail_clean)

    assert reclaim_module.reclaim_dirty_target_worktree(tmp_git_repo, main) is None
    assert _run(tmp_git_repo, "status", "--porcelain=v1", "-z").stdout == before_status
    assert _run(tmp_git_repo, "diff", "--cached", "--binary").stdout == before_cached
    assert tracked.read_text(encoding="utf-8") == "staged plus unstaged\n"
    assert untracked.read_text(encoding="utf-8") == "untracked\n"
