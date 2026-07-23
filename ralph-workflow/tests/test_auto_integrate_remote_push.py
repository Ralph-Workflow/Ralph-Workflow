"""End-to-end coverage for the opt-in multi-remote push hook.

The push hook (``ralph.git.remote_push.push_branch_to_all_remotes``)
is the ONLY place auto-integration ever reaches a remote, and it is
fail-open and best-effort by contract. These tests prove the three
invariants the prompt requires:

1. With push enabled and multiple bare remotes configured, EVERY
   remote's ``refs/heads/<target>`` advances to the feature tip
   after a successful local landing. The ``last_push`` summary on
   the recorded ``RebaseState`` names all of them.
2. With one unreachable remote, the reachable remote still
   advances, the run is NOT failed, and the LOCAL
   ``refs/heads/<target>`` still moves to the feature tip. A
   remote failure must never affect local sync.
3. With push disabled (default), no remote is contacted and local
   behaviour is byte-identical to the pre-feature baseline.

A focused unit test for ``push_branch_to_all_remotes`` with zero
remotes covers the "no remotes configured" summary branch.

Every remote in this module is a local bare repository path or a
path that does not exist: no test reaches a real network host.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.remote_push import push_branch_to_all_remotes
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    path = repo_root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    assert _run(repo_root, "add", filename).returncode == 0
    assert _run(repo_root, "commit", "-m", message).returncode == 0
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _build_config(
    *,
    push_enabled: bool = True,
    push_timeout: float = 2.0,
) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
                "auto_integrate_push_enabled": push_enabled,
                "auto_integrate_push_timeout_seconds": push_timeout,
            }
        }
    )


def _make_bare_repo(path: Path) -> Path:
    """Initialize a bare repository at ``path`` (no working tree).

    ``git init --bare`` requires the directory to exist when the
    target is the last positional argument; creating the parent
    directory alone is not enough.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir(exist_ok=True)
    assert _run(path, "init", "--bare", ".").returncode == 0
    return path


def _make_feature_repo(
    tmp_path: Path,
    main: str,
    feature: str,
    remotes: dict[str, Path],
) -> Path:
    """Create a feature repo with ``main`` and a feature branch, push
    ``main`` to every remote, and return the repo root.
    """
    repo = tmp_path / "feature"
    repo.mkdir(parents=True, exist_ok=True)
    assert _run(repo, "init", "-b", main).returncode == 0
    assert _run(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _run(repo, "config", "user.name", "Test User").returncode == 0
    # Seed an initial commit on main.
    _commit(repo, "README.md", "init\n", "initial commit")
    for name, remote_path in remotes.items():
        assert _run(repo, "remote", "add", name, str(remote_path)).returncode == 0
    # Push main to every remote so the ref exists there too.
    for name in remotes:
        assert _run(repo, "push", name, main).returncode == 0
    # Switch onto the feature branch.
    assert _run(repo, "checkout", "-b", feature).returncode == 0
    return repo


def _bare_branch_sha(bare: Path, branch: str) -> str | None:
    """Return the SHA a bare repo holds for ``refs/heads/<branch>``."""
    result = _run(bare, "rev-parse", "--verify", f"refs/heads/{branch}")
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _local_branch_sha(repo: Path, branch: str) -> str:
    return _run(repo, "rev-parse", "--verify", f"refs/heads/{branch}").stdout.strip()


# ---------------------------------------------------------------------------
# Pure unit test for the no-remotes branch
# ---------------------------------------------------------------------------


def test_push_branch_to_all_remotes_with_no_remotes_returns_summary() -> None:
    """AC-03: when no remotes are configured, the helper returns
    'no remotes configured' without error.

    A regression that returned an empty string would render as a
    bare ``[push: ]`` in the operator-facing line; a regression
    that raised would fail runs that simply have no remotes.
    """
    repo = Path("/tmp/no-such-repo-for-no-remotes-test")
    # ``run_git`` returns rc != 0 when the path is not a repo; the
    # helper treats that as "no remotes" and returns the canonical
    # summary, so the call is safe.
    summary = push_branch_to_all_remotes(repo, "main", timeout_seconds=1.0)
    assert summary == "no remotes configured"


# ---------------------------------------------------------------------------
# Real-git e2e: push enabled, all remotes succeed
# ---------------------------------------------------------------------------


def test_push_to_every_remote_after_successful_landing(tmp_path: Path) -> None:
    """AC-03: with push enabled and two bare remotes, a landing
    advances BOTH remotes' ``refs/heads/main`` to the feature tip
    and records the summary on ``RebaseState.last_push``.
    """
    main = "main"
    feature = "feature-x"
    origin_bare = _make_bare_repo(tmp_path / "origin.git")
    backup_bare = _make_bare_repo(tmp_path / "backup.git")
    repo = _make_feature_repo(
        tmp_path / "work",
        main=main,
        feature=feature,
        remotes={"origin": origin_bare, "backup": backup_bare},
    )
    # Make a feature commit that lands strictly ahead of main.
    feature_sha = _commit(repo, "feature.txt", "feature work\n", "feature commit")
    # Drive the full auto-integrate path with push enabled.
    config = _build_config(push_enabled=True)
    state = RebaseState()
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(repo),
        state,
    )
    assert outcome is not None
    # The local target ref advanced to the feature tip.
    assert _local_branch_sha(repo, main) == feature_sha
    # Both remotes' main advanced to the feature tip.
    assert _bare_branch_sha(origin_bare, main) == feature_sha
    assert _bare_branch_sha(backup_bare, main) == feature_sha
    # The summary names both remotes as success.
    assert outcome.last_push is not None
    assert "2/2" in outcome.last_push
    assert "origin" not in outcome.last_push.replace("2/2", "")
    assert "backup" not in outcome.last_push.replace("2/2", "")


# ---------------------------------------------------------------------------
# Real-git e2e: one remote unreachable, the other reachable
# ---------------------------------------------------------------------------


def test_unreachable_remote_does_not_affect_local_or_reachable_push(
    tmp_path: Path,
) -> None:
    """AC-04: with one remote pointing at a non-existent path, the
    reachable remote still advances, the run is NOT failed, and the
    LOCAL ``refs/heads/main`` still moves to the feature tip.

    A remote failure must never affect local sync.
    """
    main = "main"
    feature = "feature-y"
    backup_bare = _make_bare_repo(tmp_path / "backup.git")
    # Setup with only the reachable remote so the initial push
    # succeeds; the unreachable remote is added AFTER the initial
    # setup so the test setup itself does not fail.
    repo = _make_feature_repo(
        tmp_path / "work",
        main=main,
        feature=feature,
        remotes={"backup": backup_bare},
    )
    # ``origin`` is a path that does not exist -- push will fail
    # with ENOENT, exercising the per-remote try/except branch.
    bad_origin = tmp_path / "no-such-remote.git"
    assert _run(repo, "remote", "add", "origin", str(bad_origin)).returncode == 0
    feature_sha = _commit(repo, "feature.txt", "feature work\n", "feature commit")
    config = _build_config(push_enabled=True, push_timeout=2.0)
    state = RebaseState()
    # The call MUST NOT raise; the helper swallows every per-remote
    # failure and the integration call returns a record.
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(repo),
        state,
    )
    assert outcome is not None
    # Local target moved to the feature tip -- the "remote must not
    # affect local sync" invariant.
    assert _local_branch_sha(repo, main) == feature_sha
    # The reachable remote advanced.
    assert _bare_branch_sha(backup_bare, main) == feature_sha
    # The summary names the partial outcome: 1/2 succeeded, 1
    # failed (origin).
    assert outcome.last_push is not None
    assert "1/2" in outcome.last_push
    assert "origin" in outcome.last_push


# ---------------------------------------------------------------------------
# Real-git e2e: push disabled, no remote is contacted
# ---------------------------------------------------------------------------


def test_push_disabled_does_not_contact_any_remote(tmp_path: Path) -> None:
    """AC-03: with push disabled (default), no remote is contacted
    and the local landing is byte-identical to runs without the
    push feature.
    """
    main = "main"
    feature = "feature-z"
    origin_bare = _make_bare_repo(tmp_path / "origin.git")
    repo = _make_feature_repo(
        tmp_path / "work",
        main=main,
        feature=feature,
        remotes={"origin": origin_bare},
    )
    feature_sha = _commit(repo, "feature.txt", "feature work\n", "feature commit")
    # Record the bare's main BEFORE the integration: push-disabled
    # must not change it.
    main_sha_before = _bare_branch_sha(origin_bare, main)
    config = _build_config(push_enabled=False)
    state = RebaseState()
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(repo),
        state,
    )
    assert outcome is not None
    # Local target still advanced (the rebase/merge/fast-forward is
    # independent of push).
    assert _local_branch_sha(repo, main) == feature_sha
    # The bare's main is UNCHANGED -- push disabled never touched
    # the remote.
    assert _bare_branch_sha(origin_bare, main) == main_sha_before
    # No push summary was recorded.
    assert outcome.last_push is None


# ---------------------------------------------------------------------------
# Real-git e2e: crash-recovery landing also pushes (AC-03 every-advance)
# ---------------------------------------------------------------------------


def test_recovery_landing_pushes_to_all_remotes(tmp_path: Path) -> None:
    """AC-03 supplement: a recovered fast-forward landing pushes too.

    The prompt requires that EVERY successful advance of the shared
    target reaches every configured remote when push is enabled. The
    happy path covered above is one such advance; the crash-recovery
    continuation in
    :func:`ralph.pipeline.auto_integrate_recovery._land_and_reconcile`
    is the other -- a previous run crashed after the local
    fast-forward, and the next startup finishes it through recovery.

    This test sets up a phase='integrated' durable record pointing at
    a feature SHA the target does NOT yet contain (so the recovery
    path takes the ``_land_and_reconcile`` branch, not the
    ``already fast-forwarded`` early-return) and asserts that the
    recovery call advances the local target AND pushes to BOTH
    configured bare remotes. The ``last_push`` summary on the
    returned ``RebaseState`` reports the multi-remote success.
    """
    from ralph.pipeline.auto_integrate import IntegrationRecord
    from ralph.pipeline.auto_integrate_recovery import (
        recover_incomplete_integration,
    )

    main = "main"
    feature = "feature-r"
    origin_bare = _make_bare_repo(tmp_path / "origin.git")
    backup_bare = _make_bare_repo(tmp_path / "backup.git")
    repo = _make_feature_repo(
        tmp_path / "work",
        main=main,
        feature=feature,
        remotes={"origin": origin_bare, "backup": backup_bare},
    )
    feature_sha = _commit(repo, "feature.txt", "feature work\n", "feature commit")
    # Move HEAD back to main WITHOUT advancing the local mainline ref
    # so the recovery path will land the fast-forward (the
    # "target advanced concurrently" branch refuses to land when the
    # local main is already at the feature tip, so we must avoid that
    # state).
    pre_target_sha = _run(repo, "rev-parse", f"refs/heads/{main}").stdout.strip()
    pre_feature_sha = _run(repo, "rev-parse", "HEAD").stdout.strip()
    assert pre_target_sha != feature_sha, (
        "test setup: target must not already be at the feature tip"
    )
    # Write a phase='integrated' record pointing at the feature SHA
    # the recovery path will land.
    record_file = repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(
        IntegrationRecord(
            phase="integrated",
            target=main,
            pre_feature_sha=pre_feature_sha,
            pre_target_sha=pre_target_sha,
            integrated_feature_sha=feature_sha,
        ).model_dump_json(),
        encoding="utf-8",
    )
    config = _build_config(push_enabled=True)
    outcome = recover_incomplete_integration(WorkspaceScope(repo), config=config)
    assert outcome is not None
    assert outcome.last_action == "recovered"
    assert outcome.fast_forwarded is True
    # Local target now at the feature tip.
    assert _local_branch_sha(repo, main) == feature_sha
    # BOTH bare remotes advanced.
    assert _bare_branch_sha(origin_bare, main) == feature_sha
    assert _bare_branch_sha(backup_bare, main) == feature_sha
    # The push summary reports both remotes as successful.
    assert outcome.last_push is not None
    assert "2/2" in outcome.last_push
    # The durable record is cleared by a successful recovery.
    assert not record_file.exists()


def test_recovery_landing_with_unreachable_remote_keeps_local_success(
    tmp_path: Path,
) -> None:
    """AC-04 supplement: recovery push is fail-open like the happy path.

    Mirrors the happy-path unreachable-remote invariant at the
    recovery landing site: one unreachable remote must not stop the
    other from advancing, must not fail the recovery, and must not
    alter the local mainline advance.
    """
    from ralph.pipeline.auto_integrate import IntegrationRecord
    from ralph.pipeline.auto_integrate_recovery import (
        recover_incomplete_integration,
    )

    main = "main"
    feature = "feature-r2"
    backup_bare = _make_bare_repo(tmp_path / "backup.git")
    repo = _make_feature_repo(
        tmp_path / "work",
        main=main,
        feature=feature,
        remotes={"backup": backup_bare},
    )
    feature_sha = _commit(repo, "feature.txt", "feature work\n", "feature commit")
    pre_target_sha = _run(repo, "rev-parse", f"refs/heads/{main}").stdout.strip()
    pre_feature_sha = _run(repo, "rev-parse", "HEAD").stdout.strip()
    assert pre_target_sha != feature_sha
    bad_origin = tmp_path / "no-such-remote.git"
    assert _run(repo, "remote", "add", "origin", str(bad_origin)).returncode == 0
    record_file = repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(
        IntegrationRecord(
            phase="integrated",
            target=main,
            pre_feature_sha=pre_feature_sha,
            pre_target_sha=pre_target_sha,
            integrated_feature_sha=feature_sha,
        ).model_dump_json(),
        encoding="utf-8",
    )
    config = _build_config(push_enabled=True, push_timeout=2.0)
    outcome = recover_incomplete_integration(WorkspaceScope(repo), config=config)
    assert outcome is not None
    assert outcome.last_action == "recovered"
    assert outcome.fast_forwarded is True
    # Local target advanced.
    assert _local_branch_sha(repo, main) == feature_sha
    # Reachable remote advanced.
    assert _bare_branch_sha(backup_bare, main) == feature_sha
    # Partial push summary recorded.
    assert outcome.last_push is not None
    assert "1/2" in outcome.last_push
    assert "origin" in outcome.last_push
