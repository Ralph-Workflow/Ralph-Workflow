"""End-to-end proof for a no-origin linked-worktree fleet (AC-03, AC-04).

Ralph's own agents run as sibling ``wt-0NN-*`` worktrees over ONE git
common directory with no ``origin`` at all. In that topology the mainline
pointer other agents advance is the local ``refs/heads/<target>``, so
there is nothing to fetch -- but the previous refresh treated "no origin"
as terminal and reported ``REFRESH_NO_ORIGIN``, which is
indistinguishable from "the pointer could not be observed". Every
integration in the fleet therefore proceeded against a pointer whose
freshness nothing had confirmed.

This file proves the three things only a real multi-worktree repository
can prove: the refresh reports the distinct local-fleet outcome, a
sibling that advances the target mid-flight is picked up, and the
worktree-conflict precondition answers correctly in BOTH directions --
including for the primary worktree, which the old HEAD-file scan could
not see at all.

File-level markers. ``subprocess_e2e`` keeps this out of ``make test``'s
budget-tracked 60 s step. ``timeout_seconds(30)`` sizes the budget for
building a repository with two linked worktrees; no shared suite cap is
raised.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.merge import branch_sha
from ralph.git.rebase.rebase_preconditions import (
    RebasePreconditionError,
    check_rebase_preconditions,
)
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_refresh import refresh_target
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_LOCAL_FLEET,
    REFRESH_NO_ORIGIN,
    observe_target_sha,
    refresh_target_from_remote,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]

#: Prefix-sharing branch names, re-pinning the regression recorded in
#: ``ralph/git/rebase/_worktree_head_ref.py``: a sibling worktree on
#: ``wt-040-fix-autorebase`` must not read as holding ``wt-040``.
_SIBLING_BRANCH = "wt-040"
_FEATURE_BRANCH = "wt-040-fix-autorebase"


def _run(
    repo_root: Path, *args: str, timeout: float = 20.0
) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in ``repo_root``."""
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _base_branch(tmp_git_repo: Path) -> str:
    out = _run(tmp_git_repo, "symbolic-ref", "--quiet", "HEAD")
    return out.stdout.strip().removeprefix("refs/heads/")


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run(repo_root, "add", filename)
    _run(repo_root, "commit", "-m", message)
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _build_config(target: str, *, fetch_enabled: bool = False) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
                "auto_integrate_fetch_enabled": fetch_enabled,
            }
        }
    )


def _fleet(tmp_git_repo: Path, tmp_path: Path) -> tuple[str, Path]:
    """A no-origin repo whose feature branch lives in a linked worktree.

    Returns ``(target_branch, feature_worktree_root)``. The PRIMARY
    checkout keeps the target branch, exactly as a real fleet's parent
    repository does, and a sibling worktree holds the prefix-sharing
    ``wt-040`` branch.
    """
    target = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "seed.txt", "seed\n", "seed")
    _run(tmp_git_repo, "remote", "remove", "origin")

    sibling = tmp_path / "wt-sibling"
    _run(tmp_git_repo, "worktree", "add", "-b", _SIBLING_BRANCH, str(sibling))
    feature = tmp_path / "wt-feature"
    _run(tmp_git_repo, "worktree", "add", "-b", _FEATURE_BRANCH, str(feature))
    _commit(feature, "feature.txt", "feature work\n", "feature work")
    return target, feature


def test_no_origin_with_a_local_target_reports_the_local_fleet_outcome(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    """The distinct outcome an operator needs: observed, not unobservable."""
    target, feature = _fleet(tmp_git_repo, tmp_path)

    outcome = refresh_target_from_remote(feature, target, timeout_seconds=5.0)

    assert outcome == REFRESH_LOCAL_FLEET


def test_no_origin_and_no_such_branch_still_reports_no_origin(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    """The genuinely unobservable case keeps its own distinct outcome."""
    _target, feature = _fleet(tmp_git_repo, tmp_path)

    outcome = refresh_target_from_remote(
        feature, "no-such-branch", timeout_seconds=5.0
    )

    assert outcome == REFRESH_NO_ORIGIN


def test_disabling_the_fetch_does_not_disable_local_pointer_freshness(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    """``auto_integrate_fetch_enabled`` governs the network, nothing else."""
    target, feature = _fleet(tmp_git_repo, tmp_path)

    outcome = refresh_target(
        _build_config(target, fetch_enabled=False), feature, target
    )

    assert outcome == REFRESH_LOCAL_FLEET


def test_the_observation_reads_the_ref_a_sibling_just_advanced(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    """Branch refs live in the COMMON dir, so a sibling's landing is visible."""
    target, feature = _fleet(tmp_git_repo, tmp_path)
    before = observe_target_sha(feature, target)

    advanced = _commit(tmp_git_repo, "sibling.txt", "sibling\n", "sibling landed")

    assert before != advanced
    assert observe_target_sha(feature, target) == advanced


def test_a_sibling_advancing_the_target_mid_run_is_still_landed(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    """The fleet requirement end to end: rebase onto the NEW tip and land."""
    target, feature = _fleet(tmp_git_repo, tmp_path)
    # A sibling agent lands on the mainline after this worktree committed.
    advanced = _commit(tmp_git_repo, "sibling.txt", "sibling\n", "sibling landed")

    outcome = auto_integrate_after_commit(
        _build_config(target), WorkspaceScope(feature), RebaseState()
    )

    assert outcome is not None
    assert outcome.last_refresh == REFRESH_LOCAL_FLEET
    feature_head = _run(feature, "rev-parse", "HEAD").stdout.strip()
    # The replay really happened on top of the sibling's commit.
    assert (
        _run(feature, "merge-base", "--is-ancestor", advanced, "HEAD").returncode == 0
    )
    assert branch_sha(feature, target) == feature_head


def test_a_prefix_sharing_sibling_branch_does_not_block_the_rebase(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    """``wt-040`` checked out elsewhere must not read as holding ``wt-040-*``."""
    _target, feature = _fleet(tmp_git_repo, tmp_path)

    check_rebase_preconditions(feature)


def test_a_branch_held_by_the_primary_worktree_blocks_the_rebase(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    """The case the old HEAD-file scan was structurally blind to.

    ``<common_dir>/worktrees/`` has no entry for the PRIMARY checkout, so
    the previous implementation could never report the primary as holding
    a branch -- while the fast-forward side, asking ``git worktree list``,
    could. Two answers to one question. Asking git in both places is what
    removed the divergence.

    The second checkout is built by writing HEAD's symref directly, which
    is the only way to reach the state at all: ``git checkout`` refuses to
    put a branch in two worktrees, and that refusal is exactly the
    invariant this precondition mirrors for rebases.
    """
    target, _feature = _fleet(tmp_git_repo, tmp_path)
    second = tmp_path / "wt-also-on-target"
    _run(tmp_git_repo, "worktree", "add", "--detach", str(second))
    _run(second, "symbolic-ref", "HEAD", f"refs/heads/{target}")

    with pytest.raises(RebasePreconditionError, match="already checked out"):
        check_rebase_preconditions(second)
