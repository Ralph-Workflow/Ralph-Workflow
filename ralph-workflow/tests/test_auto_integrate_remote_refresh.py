"""Regression coverage for the observe-only remote refresh of the target ref.

Auto-integration is a LOCAL feature: every rebase, merge and landing
decision is made against the local ``refs/heads/<target>`` the fleet of
linked worktrees advances directly. A configured origin may be fetched
(when ``auto_integrate_fetch_enabled`` is explicitly turned on) purely
to OBSERVE and report its position -- remote state must never move a
local ref or otherwise affect local rebase operations. These tests
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
from ralph.git.merge import branch_sha, is_ancestor
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
        timeout=10,
    )


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    path = repo_root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    assert _run(repo_root, "add", filename).returncode == 0
    assert _run(repo_root, "commit", "-m", message).returncode == 0
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _build_config(*, fetch_timeout: float = 2.0) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_fetch_enabled": True,
                "auto_integrate_fetch_timeout_seconds": fetch_timeout,
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
    assert (
        _run(tmp_git_repo, "clone", "--bare", str(tmp_git_repo), str(bare)).returncode
        == 0
    )
    return bare, main


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(20)
def test_remote_advance_never_affects_the_local_integration(
    tmp_git_repo: Path,
) -> None:
    """A mainline pushed to origin by another clone stays on origin.

    Integration reasons about the LOCAL target ref only: the commit the
    other clone pushed must not be rebased in, must not appear in the
    worktree, and the landing must fast-forward the local ref from its
    own (locally observed) position.
    """
    bare, main = _seed_bare_origin(tmp_git_repo)
    agent = _make_clone(bare, tmp_git_repo.parent / "agent-a", main, branch="feature")
    other = _make_clone(bare, tmp_git_repo.parent / "agent-b", main, branch="other")

    # The OTHER agent lands a commit on origin's mainline.
    assert _run(other, "checkout", main).returncode == 0
    other_sha = _commit(other, "other.txt", "other agent\n", "other agent change")
    assert _run(other, "push", "origin", main).returncode == 0

    local_main = branch_sha(agent, main)
    assert local_main is not None
    assert local_main != other_sha, "origin must genuinely be ahead"

    _commit(agent, "feature.txt", "feature\n", "feature change")
    outcome = auto_integrate_after_commit(
        _build_config(),
        WorkspaceScope(agent),
        RebaseState(),
        sleep=lambda _seconds: None,
        jitter=lambda: 0.0,
    )
    feature_head = _run(agent, "rev-parse", "HEAD").stdout.strip()

    assert outcome is not None
    assert outcome.fast_forwarded is True
    assert is_ancestor(agent, other_sha, feature_head) is False, (
        "a commit that exists only on origin was integrated locally"
    )
    assert not (agent / "other.txt").exists()
    assert branch_sha(agent, main) == feature_head


def test_unreachable_remote_degrades_to_local_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-03 fail-open: an unreachable origin must not fail the run."""
    monkeypatch.setattr(auto_integrate_sync, "_has_origin", lambda _root: True)
    monkeypatch.setattr(
        auto_integrate_sync,
        "_fetch_target",
        lambda _root, _target, _timeout: False,
    )

    assert (
        refresh_target_from_remote(
            Path("/workspace"), "main", timeout_seconds=2.0
        )
        == REFRESH_UNREACHABLE
    )


def _inject_remote_position(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ancestor: bool,
) -> None:
    """Inject a successful fetch and deterministic local/remote tips."""
    monkeypatch.setattr(auto_integrate_sync, "_has_origin", lambda _root: True)
    monkeypatch.setattr(
        auto_integrate_sync,
        "_fetch_target",
        lambda _root, _target, _timeout: True,
    )
    monkeypatch.setattr(
        auto_integrate_sync, "_remote_tracking_sha", lambda _root, _target: "remote"
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
        refresh_target_from_remote(
            Path("/workspace"), "main", timeout_seconds=2.0
        )
        == REFRESH_DIVERGED
    )


def test_retry_attempt_reintegrates_locally_without_pulling_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bounded retry re-observes the LOCAL pointer, never origin.

    The first attempt loses the landing race exactly as it would in
    production. The retry must integrate onto the local mainline as it
    is NOW -- and only the local mainline: a commit pushed to origin
    between the attempts must stay on origin.
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
    monkeypatch.setattr(
        auto_integrate, "resolver_allowed", lambda _state, _target, _identity: True
    )
    monkeypatch.setattr(
        auto_integrate,
        "_refresh_target",
        lambda _config, _root, _target: events.append("refresh") or "origin ahead",
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
        _build_config(),
        WorkspaceScope(root),
        RebaseState(),
        sleep=lambda _seconds: events.append("backoff"),
        jitter=lambda: 0.0,
    )

    assert outcome is not None
    assert outcome.fast_forwarded is True
    assert events == ["integrate", "backoff", "refresh", "integrate"]


def test_refresh_never_moves_the_local_ref_even_when_origin_is_ahead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote observation is read-only for LOCAL refs, with no exception.

    Auto-integration is a local-only feature: the mainline pointer every
    rebase and landing decision uses is ``refs/heads/<target>``, owned by
    the local fleet. A fetch may observe origin, but an origin that is
    strictly ahead must NOT be applied to the local ref -- the previous
    behaviour, which let a remote nobody asked about rewrite the base of
    every local rebase.
    """
    _inject_remote_position(monkeypatch, ancestor=True)
    assert (
        refresh_target_from_remote(
            Path("/workspace"), "main", timeout_seconds=2.0
        )
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
    monkeypatch.setattr(auto_integrate_sync, "_has_origin", lambda _root: True)
    monkeypatch.setattr(
        auto_integrate_sync,
        "_fetch_target",
        lambda _root, _target, _timeout: False,
    )
    monkeypatch.setattr(
        auto_integrate_sync,
        "_classify_remote_position",
        lambda _root, _target: pytest.fail("used a stale remote-tracking ref"),
    )

    assert (
        refresh_target_from_remote(
            Path("/workspace"), "main", timeout_seconds=2.0
        )
        == REFRESH_UNREACHABLE
    )
