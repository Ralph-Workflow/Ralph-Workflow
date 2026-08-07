"""Regression coverage for remote-sync target refresh.

Auto-integration is a LOCAL feature: every rebase, merge and landing
decision is made against the local ``refs/heads/<target>`` the fleet of
linked worktrees advances directly. The opt-in remote-sync path may fetch its configured remote purely to
observe and reconcile it; local-only integration never fetches. These tests
prove both halves: the observation stays read-only, and integration
proceeds against the local pointer regardless of what origin holds.

Every remote in this module is a local bare repository path or a path
that does not exist: no test reaches a real network host.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.merge import branch_sha
from ralph.pipeline import auto_integrate, auto_integrate_sync
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_ff import is_retryable_fast_forward_failure
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_DIVERGED,
    REFRESH_ORIGIN_AHEAD,
    REFRESH_UNREACHABLE,
    refresh_target_from_remote,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

#: A fast-forward skip reason the bounded retry loop treats as a
#: transient concurrent target move (asserted below, so the literal
#: cannot silently drift away from the production set).
_CONCURRENT_MOVE = "target advanced concurrently (CAS mismatch)"


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    path = repo_root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    assert _run(repo_root, "add", filename).returncode == 0
    assert _run(repo_root, "commit", "-m", message).returncode == 0
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _build_config(*, remote_enabled: bool = False) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_remote_enabled": remote_enabled,
            }
        }
    )


def _make_clone(bare: Path, path: Path, main: str, *, branch: str) -> Path:
    """Clone-topology checkout with a materialized local ``main``."""
    path.mkdir()
    assert _run(path, "init").returncode == 0
    assert _run(path, "config", "user.email", "test@example.com").returncode == 0
    assert _run(path, "config", "user.name", "Test User").returncode == 0
    assert _run(path, "remote", "add", "origin", str(bare)).returncode == 0
    assert _run(path, "fetch", "origin", main).returncode == 0
    assert _run(path, "checkout", "-b", main, f"origin/{main}").returncode == 0
    assert _run(path, "checkout", "-b", branch).returncode == 0
    return path


def _seed_bare_origin(tmp_git_repo: Path) -> tuple[Path, str]:
    """Return ``(bare_origin_path, main_branch_name)``."""
    main = _run(tmp_git_repo, "branch", "--show-current").stdout.strip()
    bare = tmp_git_repo.parent / "origin.git"
    assert _run(tmp_git_repo, "clone", "--bare", str(tmp_git_repo), str(bare)).returncode == 0
    return bare, main


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(20)
def test_remote_ahead_refresh_keeps_the_local_target_unchanged(
    tmp_git_repo: Path,
) -> None:
    """A real fetch observes origin ahead without moving the local target."""
    bare, main = _seed_bare_origin(tmp_git_repo)
    agent = _make_clone(bare, tmp_git_repo.parent / "agent-a", main, branch="feature")

    local_main = branch_sha(agent, main)
    assert local_main is not None
    remote_sha = _commit(tmp_git_repo, "remote.txt", "remote advance\n", "remote advance")
    assert _run(tmp_git_repo, "push", str(bare), main).returncode == 0
    assert remote_sha != local_main

    outcome = refresh_target_from_remote(agent, main, timeout_seconds=2.0)

    assert outcome == REFRESH_ORIGIN_AHEAD
    assert branch_sha(agent, main) == local_main
    assert not (agent / "remote.txt").exists()


def test_unreachable_remote_degrades_to_local_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-03 fail-open: an unreachable origin must not fail the run."""
    monkeypatch.setattr(auto_integrate_sync, "_has_remote", lambda _root, _remote="origin": True)
    monkeypatch.setattr(
        auto_integrate_sync,
        "_fetch_target",
        lambda _root, _target, _timeout, *, remote=None: False,
    )

    assert (
        refresh_target_from_remote(Path("/workspace"), "main", timeout_seconds=2.0)
        == REFRESH_UNREACHABLE
    )


def _inject_remote_position(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ancestor: bool,
) -> None:
    """Inject a successful fetch and deterministic local/remote tips."""
    monkeypatch.setattr(auto_integrate_sync, "_has_remote", lambda _root, _remote="origin": True)
    monkeypatch.setattr(
        auto_integrate_sync,
        "_fetch_target",
        lambda _root, _target, _timeout, *, remote=None: True,
    )
    monkeypatch.setattr(
        auto_integrate_sync,
        "_remote_tracking_sha",
        lambda _root, _target, _remote="origin": "remote",
    )
    monkeypatch.setattr(auto_integrate_sync, "branch_sha", lambda _root, _target: "local")
    monkeypatch.setattr(
        auto_integrate_sync,
        "is_ancestor",
        lambda _root, _ancestor, _descendant: ancestor,
    )


def test_diverged_remote_is_not_force_moved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-03: a diverged origin must never force-move the local mainline."""
    _inject_remote_position(monkeypatch, ancestor=False)
    assert (
        refresh_target_from_remote(Path("/workspace"), "main", timeout_seconds=2.0)
        == REFRESH_DIVERGED
    )


def test_retry_attempt_refetches_and_reclassifies_the_remote_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression S-4: every retry refreshes the configured remote base.

    A first landing can lose its CAS race after a healthy fetch. The retry must
    re-run the same remote freshness pipeline before it rebases again rather
    than treating a local ref observation as proof that origin is still fresh.
    """
    assert is_retryable_fast_forward_failure(_CONCURRENT_MOVE) is True

    root = Path("/workspace")
    events: list[str] = []
    retries = iter((True, False))
    monkeypatch.setattr(
        auto_integrate,
        "_auto_integrate_resolve_context",
        lambda _config, _scope: (root, "feature", "main", "origin ahead"),
    )
    monkeypatch.setattr(
        auto_integrate,
        "_auto_integrate_check_skip_conditions",
        lambda _root, _branch, _target: None,
    )
    monkeypatch.setattr(
        auto_integrate, "observe_conflict_identity", lambda _root, _target: "identity"
    )
    monkeypatch.setattr(auto_integrate, "resolver_allowed", lambda _state, _target, _identity: True)
    monkeypatch.setattr(
        auto_integrate,
        "pull_and_reconcile_target",
        lambda *_args, **_kwargs: events.append("remote refresh")
        or RebaseState(
            last_remote_sync="already current",
            freshness_verdict="verified",
            freshness_source="fetch",
        ),
    )

    def _integrate_once(*_args: object, **_kwargs: object) -> tuple[RebaseState, bool]:
        events.append("integrate")
        return (
            RebaseState(
                last_action="rebased",
                last_target="main",
                fast_forwarded=True,
            ),
            next(retries),
        )

    monkeypatch.setattr(auto_integrate, "_integrate_once", _integrate_once)
    outcome = auto_integrate_after_commit(
        _build_config(remote_enabled=True),
        WorkspaceScope(root),
        RebaseState(),
        sleep=lambda _seconds: events.append("backoff"),
        jitter=lambda: 0.0,
    )

    assert outcome is not None
    assert outcome.fast_forwarded is True
    assert events == ["remote refresh", "integrate", "backoff", "remote refresh", "integrate"]


def test_refresh_observation_never_moves_the_local_ref_even_when_origin_is_ahead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read-only probe reports an ahead remote; its caller applies it safely.

    Remote target movement belongs exclusively to the worktree-aware
    ``pull_and_reconcile_target`` seam. The observation helper itself remains
    read-only, preserving local-target resolution and preventing a fetch from
    silently changing the base before the shared advancement contract runs.
    """
    _inject_remote_position(monkeypatch, ancestor=True)
    assert (
        refresh_target_from_remote(Path("/workspace"), "main", timeout_seconds=2.0)
        == REFRESH_ORIGIN_AHEAD
    )


def test_refresh_regression_failed_fetch_never_claims_a_fresh_origin_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cached remote-tracking ref is not evidence of a fresh origin read.

    The refresh used to fall through to the advance whenever
    ``refs/remotes/origin/<target>`` existed, even when the fetch meant
    to update it had just failed. It then advanced the shared local ref
    and reported ``refreshed from origin`` -- a freshness claim about a
    pointer that can be arbitrarily old. The only outcome an
    unreachable origin may produce is ``origin unreachable``.
    """
    monkeypatch.setattr(auto_integrate_sync, "_has_remote", lambda _root, _remote="origin": True)
    monkeypatch.setattr(
        auto_integrate_sync,
        "_fetch_target",
        lambda _root, _target, _timeout, *, remote=None: False,
    )
    monkeypatch.setattr(
        auto_integrate_sync,
        "_classify_remote_position",
        lambda _root, _target: pytest.fail("used a stale remote-tracking ref"),
    )

    assert (
        refresh_target_from_remote(Path("/workspace"), "main", timeout_seconds=2.0)
        == REFRESH_UNREACHABLE
    )
