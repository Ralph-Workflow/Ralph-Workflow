"""Regression coverage: a stale merge marker must not disable integration.

Git's ``ort`` merge strategy writes ``AUTO_MERGE`` into the worktree's
private git dir whenever a merge stops on conflicts, and neither
``merge --abort`` nor the follow-up merge commit removes it. The rebase
preconditions used to reject any git-dir entry whose name contained
``MERGE``, so the FIRST conflicted merge permanently disabled
auto-integration for that worktree.
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

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    assert _run(repo_root, "add", filename).returncode == 0
    assert _run(repo_root, "commit", "-m", message).returncode == 0
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _build_config(*, target: str) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_target": target}}
    )


def _add_worktree(repo_root: Path, path: Path, branch: str) -> None:
    assert _run(repo_root, "worktree", "add", "-b", branch, str(path)).returncode == 0


def _private_git_dir(worktree: Path) -> Path:
    """Resolve a linked worktree's private git dir.

    The ``.git`` entry of a linked worktree is a FILE holding
    ``gitdir: <path>``; that path is where git's ``ort`` strategy
    writes ``AUTO_MERGE``.
    """
    pointer = worktree / ".git"
    raw = pointer.read_text(encoding="utf-8").split(":", 1)[1].strip()
    return Path(raw)


def test_stale_auto_merge_marker_does_not_block_integration(
    tmp_git_repo: Path,
) -> None:
    """AC-01: a leftover ``AUTO_MERGE`` must not skip the integration."""
    main = _base_branch(tmp_git_repo)
    feature = tmp_git_repo.parent / "feature-stale-marker"
    _add_worktree(tmp_git_repo, feature, "feature-stale-marker")
    try:
        _commit(feature, "feature.txt", "feature\n", "feature change")
        (_private_git_dir(feature) / "AUTO_MERGE").write_text("", encoding="utf-8")

        outcome = auto_integrate_after_commit(
            _build_config(target=main), WorkspaceScope(feature), RebaseState()
        )
        feature_head = _run(feature, "rev-parse", "HEAD").stdout.strip()

        assert outcome is not None
        assert outcome.fast_forwarded is True
        assert outcome.last_action in {"rebased", "merged"}
        assert branch_sha(tmp_git_repo, main) == feature_head
        reason = outcome.last_reason or ""
        assert not reason.startswith("preconditions not met")
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature))


def test_synthetic_clean_merge_head_is_reclaimed_and_lands(
    tmp_git_repo: Path,
) -> None:
    """A5: a synthetic MERGE_HEAD on a clean tree is reclaimed and lands."""
    main = _base_branch(tmp_git_repo)
    feature = tmp_git_repo.parent / "feature-real-merge"
    _add_worktree(tmp_git_repo, feature, "feature-real-merge")
    try:
        _commit(feature, "feature.txt", "feature\n", "feature change")
        _commit(tmp_git_repo, "main.txt", "main\n", "main change")
        (_private_git_dir(feature) / "MERGE_HEAD").write_text(
            "0" * 40, encoding="utf-8"
        )

        outcome = auto_integrate_after_commit(
            _build_config(target=main), WorkspaceScope(feature), RebaseState()
        )

        assert outcome is not None
        assert outcome.fast_forwarded is True
        assert outcome.last_action in {"rebased", "merged"}
        assert branch_sha(tmp_git_repo, main) == _run(feature, "rev-parse", "HEAD").stdout.strip()
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature))


def test_real_dirty_merge_conflict_is_protected_and_skipped(tmp_git_repo: Path) -> None:
    """A5: a real dirty merge conflict remains operator-owned."""
    main = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed")
    feature = tmp_git_repo.parent / "feature-real-merge"
    _add_worktree(tmp_git_repo, feature, "feature-real-merge")
    try:
        _commit(feature, "shared.txt", "feature\n", "feature change")
        _commit(tmp_git_repo, "shared.txt", "main\n", "main change")
        merge = _run(feature, "merge", main)
        assert merge.returncode != 0
        git_dir = _private_git_dir(feature)
        assert (git_dir / "MERGE_HEAD").exists()
        before_status = _run(feature, "status", "--porcelain").stdout

        outcome = auto_integrate_after_commit(
            _build_config(target=main), WorkspaceScope(feature), RebaseState()
        )

        assert outcome is not None
        assert outcome.last_action == "skipped"
        assert "preconditions not met" in (outcome.last_reason or "")
        assert (git_dir / "MERGE_HEAD").exists()
        assert _run(feature, "status", "--porcelain").stdout == before_status
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature))
