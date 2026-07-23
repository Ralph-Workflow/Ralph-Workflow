"""Local-fleet freshness contracts with one representative real worktree race.

Refresh classification is proven through the bounded Git adapter seam, so those
cases do not repeatedly create repositories and worktrees. The sole E2E case
retains the unique interaction: a sibling moves a shared branch ref while a
linked worktree is paused in a conflicted rebase.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.git_run_result import GitRunResult
from ralph.git.merge import branch_sha
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_refresh import refresh_target
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_DISABLED,
    REFRESH_LOCAL_FLEET,
    REFRESH_NO_ORIGIN,
    observe_target_sha,
    refresh_target_from_remote,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution import RebaseStop

_RESOLVED_CONTENT = "resolved by the stub resolver\n"


def _git_result(args: tuple[str, ...], *, stdout: str = "", returncode: int = 0) -> GitRunResult:
    return GitRunResult(args=args, returncode=returncode, stdout=stdout, stderr="")


def test_no_origin_classifies_an_observable_local_target_as_a_fleet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local branch is fresh evidence even when no remote exists."""
    import ralph.pipeline.auto_integrate_sync as sync

    def _run_git(args: tuple[str, ...], **_kwargs: object) -> GitRunResult:
        if args == ("remote", "get-url", "origin"):
            return _git_result(args, returncode=2)
        return _git_result(args, stdout="abc123\n")

    monkeypatch.setattr(sync, "run_git", _run_git)

    assert (
        refresh_target_from_remote(Path("/fleet"), "main", timeout_seconds=5.0)
        == REFRESH_LOCAL_FLEET
    )


def test_no_origin_without_a_local_target_remains_unobservable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No remote and no branch reports the distinct no-origin outcome."""
    import ralph.pipeline.auto_integrate_sync as sync

    def _run_git(args: tuple[str, ...], **_kwargs: object) -> GitRunResult:
        return _git_result(args, returncode=2)

    monkeypatch.setattr(sync, "run_git", _run_git)

    assert (
        refresh_target_from_remote(Path("/fleet"), "missing", timeout_seconds=5.0)
        == REFRESH_NO_ORIGIN
    )


@pytest.mark.parametrize(
    ("observed_sha", "expected"),
    [("abc123", REFRESH_LOCAL_FLEET), (None, REFRESH_DISABLED)],
)
def test_fetch_disabled_still_observes_the_local_pointer(
    monkeypatch: pytest.MonkeyPatch,
    observed_sha: str | None,
    expected: str,
) -> None:
    """The fetch flag governs network access, not shared-ref observation."""
    import ralph.pipeline.auto_integrate_refresh as refresh

    def _observe_target_sha(_root: Path, _target: str) -> str | None:
        return observed_sha

    monkeypatch.setattr(refresh, "observe_target_sha", _observe_target_sha)
    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
                "auto_integrate_fetch_enabled": False,
            }
        }
    )

    assert refresh_target(config, Path("/fleet"), "main") == expected


def test_observation_returns_the_latest_adapter_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each observation performs a fresh read through the Git adapter."""
    import ralph.pipeline.auto_integrate_sync as sync

    values = iter(("before\n", "after\n"))

    def _run_git(args: tuple[str, ...], **_kwargs: object) -> GitRunResult:
        return _git_result(args, stdout=next(values))

    monkeypatch.setattr(sync, "run_git", _run_git)

    assert observe_target_sha(Path("/fleet"), "main") == "before"
    assert observe_target_sha(Path("/fleet"), "main") == "after"


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    path = repo_root / filename
    path.write_text(content, encoding="utf-8")
    assert _run(repo_root, "add", filename).returncode == 0
    assert _run(repo_root, "commit", "-m", message).returncode == 0
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(20)
def test_sibling_target_move_during_rebase_is_reobserved_and_landed(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    """A shared target moving mid-rebase triggers a linear retry and landing."""
    target = (
        _run(tmp_git_repo, "symbolic-ref", "--quiet", "HEAD")
        .stdout.strip()
        .removeprefix("refs/heads/")
    )
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed shared")
    feature = tmp_path / "feature"
    assert _run(
        tmp_git_repo, "worktree", "add", "-b", "feature", str(feature)
    ).returncode == 0
    _commit(feature, "shared.txt", "feature\n", "feature edit")
    _commit(tmp_git_repo, "shared.txt", "target\n", "target edit")
    advanced: list[str] = []

    def _resolve(root: Path, _target: str, stop: RebaseStop) -> bool:
        if not advanced:
            advanced.append(
                _commit(tmp_git_repo, "sibling.txt", "sibling\n", "sibling landed")
            )
        for relative in stop.conflicted_files:
            (root / relative).write_text(_RESOLVED_CONTENT, encoding="utf-8")
        return True

    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
                "auto_integrate_fetch_enabled": False,
            }
        }
    )
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(feature),
        RebaseState(),
        rebase_stop_resolver=_resolve,
        sleep=lambda _seconds: None,
        jitter=lambda: 0.0,
    )

    assert outcome is not None
    assert outcome.last_refresh == REFRESH_LOCAL_FLEET
    assert outcome.last_action == "rebased"
    assert outcome.fast_forwarded is True
    assert advanced
    feature_tip = _run(feature, "rev-parse", "HEAD").stdout.strip()
    assert branch_sha(feature, target) == feature_tip
    assert (
        _run(feature, "merge-base", "--is-ancestor", advanced[0], feature_tip).returncode
        == 0
    )
    assert _run(feature, "log", "--merges", "--format=%H").stdout.strip() == ""
    assert _run(feature, "show", "HEAD:shared.txt").stdout == _RESOLVED_CONTENT
