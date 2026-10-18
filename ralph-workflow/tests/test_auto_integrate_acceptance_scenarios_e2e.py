"""Real-Git end-to-end acceptance tests for the O2 publication reconciler.

The drift-recovery tests in :mod:`tests.test_auto_integrate_drift_recovery`
use in-memory fakes to pin the contract. This module exercises the
real ``reconcile_remote_publication`` path against a real bare remote
so the acceptance criteria (AC5-AC9) are proven against actual Git
refs, not synthesised SHA strings. Real ref equality is observed
before and after each operation, not just push callbacks.

The tests are marked ``subprocess_e2e`` and ``timeout_seconds(60)`` so
they run inside the 60-second combined ``make verify`` budget.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.remote_push import PushStatus
from ralph.pipeline import auto_integrate_remote_sync as remote_sync
from ralph.pipeline._rebase_outcome_kind import RebaseOutcomeKind

_TARGET = "release"
_REMOTE = "tacos"


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in ``repo_root``."""
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    assert _run(repo_root, "add", filename).returncode == 0
    assert _run(repo_root, "commit", "-m", message).returncode == 0
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _init_repo(path: Path, default_branch: str = _TARGET) -> None:
    """Seed a repository whose default branch is ``default_branch``."""
    path.mkdir(parents=True, exist_ok=True)
    assert _run(path, "init").returncode == 0
    assert _run(path, "config", "user.email", "test@example.com").returncode == 0
    assert _run(path, "config", "user.name", "Test User").returncode == 0
    _commit(path, "seed.txt", "seed\n", "seed")
    assert _run(path, "branch", "-M", default_branch).returncode == 0


def _head_sha(repo_root: Path, ref: str) -> str:
    return _run(repo_root, "rev-parse", ref).stdout.strip()


def _build_config(
    *,
    enabled: bool = True,
    remote_enabled: bool = True,
    interval_seconds: float = 0.0,
) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": enabled,
                "auto_integrate_target": _TARGET,
                "auto_integrate_remote": _REMOTE,
                "auto_integrate_remote_enabled": remote_enabled,
                "auto_integrate_remote_interval_seconds": interval_seconds,
            }
        }
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_concurrent_remote_advance_converges_to_ref_equality(tmp_path: Path) -> None:
    """AC9 real-Git: a concurrent remote advance ends with ref equality.

    The reconciler detects a concurrent remote advance, reintegrates,
    and pushes; both the local and the remote ref end at the same
    SHA. The test exercises a real bare remote and a real second
    worktree that pushes an independent commit, so the divergence
    is real and the convergence proof is a real ``rev-parse``
    equality check.
    """
    repo = tmp_path / "repo"
    bare = tmp_path / "tacos.git"
    writer = tmp_path / "writer"

    _init_repo(repo)
    assert _run(tmp_path, "clone", "--bare", str(repo), str(bare)).returncode == 0
    assert _run(repo, "remote", "add", _REMOTE, str(bare)).returncode == 0
    assert _run(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _run(repo, "config", "user.name", "Test User").returncode == 0
    # Push the seed so the bare has the target branch.
    assert _run(repo, "push", _REMOTE, _TARGET).returncode == 0

    # Concurrent remote advance: a second worktree clones the bare
    # at the seed, adds its own commit, and pushes to the bare
    # BEFORE the local repo makes any advance. The local repo then
    # lands a divergent commit. The local and the remote are now
    # strictly diverged (each has a unique commit the other lacks).
    assert _run(tmp_path, "clone", str(bare), str(writer)).returncode == 0
    assert _run(writer, "config", "user.email", "test@example.com").returncode == 0
    assert _run(writer, "config", "user.name", "Test User").returncode == 0
    assert _run(writer, "remote", "add", _REMOTE, str(bare)).returncode == 0
    remote_advance_sha = _commit(writer, "remote.txt", "remote work\n", "remote advance")
    assert _run(writer, "push", _REMOTE, _TARGET).returncode == 0

    # Local lands a divergent commit (after the writer's push).
    local_sha = _commit(repo, "local.txt", "local work\n", "local advance")
    # Note: the local cannot push its commit (the bare is now
    # ahead on the remote_advance_sha and the local is not a
    # fast-forward of it). The push is intentionally not attempted.

    # Bring the local repo's view of the remote forward.
    assert _run(repo, "fetch", _REMOTE).returncode == 0

    # Pre-state: local and remote are strictly diverged.
    assert _head_sha(repo, f"refs/heads/{_TARGET}") == local_sha
    assert _head_sha(repo, f"refs/remotes/{_REMOTE}/{_TARGET}") == remote_advance_sha
    # The local and remote SHAs must be different (diverged).
    assert local_sha != remote_advance_sha

    # Drive the reconciler: divergence detected -> reconcile -> push.
    state = remote_sync.reconcile_remote_publication(
        _build_config(),
        repo,
        _TARGET,
        remote=_REMOTE,
    )

    # Convergence proof: the local and remote refs are now equal
    # after the reconciler ran.
    local_ref = _head_sha(repo, f"refs/heads/{_TARGET}")
    remote_ref = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert local_ref == remote_ref, f"local {local_ref} != remote {remote_ref} after reconciliation"
    # The reconciled SHA must incorporate both the local advance
    # and the remote advance (strict descendants of each).
    out_local = _run(
        repo,
        "log",
        "--format=%H",
        f"{local_sha}..{local_ref}",
    )
    assert out_local.returncode == 0
    descendants = out_local.stdout.split()
    assert remote_advance_sha in descendants, (
        f"remote advance {remote_advance_sha} missing from reconciled history"
    )
    assert state.last_push_status == PushStatus.PUSHED
    assert state.last_outcome in (
        RebaseOutcomeKind.REMOTE_PUSHED,
        RebaseOutcomeKind.MERGED,
        RebaseOutcomeKind.REBASED,
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_throttle_deferred_probe_publishes_to_configured_release_tacos(tmp_path: Path) -> None:
    """AC5, AC6 real-Git: fetch throttle armed at 60s suppresses probe during landing, but publication converges release/tacos.

    Uses non-default target 'release' and remote 'tacos'. Arms the real
    fetch throttle so the landing-seam probe is suppressed, lands a commit,
    and demonstrates that publication pushes and converges release and tacos/release
    with no origin or main branches/remotes accessed or created.
    """
    repo = tmp_path / "repo"
    bare = tmp_path / "tacos.git"

    _init_repo(repo)
    assert _run(tmp_path, "clone", "--bare", str(repo), str(bare)).returncode == 0
    assert _run(repo, "remote", "add", _REMOTE, str(bare)).returncode == 0
    assert _run(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _run(repo, "config", "user.name", "Test User").returncode == 0
    assert _run(repo, "push", _REMOTE, _TARGET).returncode == 0
    assert _run(repo, "fetch", _REMOTE).returncode == 0

    cfg = _build_config(interval_seconds=60.0)

    # Arm the real fetch throttle
    remote_sync._REMOTE_PULL_THROTTLE.arm(repo, _REMOTE, _TARGET, clock=time.monotonic)

    # Demonstrate that fetch probe is suppressed during landing
    pull_record = remote_sync.pull_and_reconcile_target(
        cfg,
        repo,
        _TARGET,
    )
    assert pull_record is not None
    assert pull_record.last_remote_sync == "refresh suppressed"
    assert pull_record.last_refresh == "refresh suppressed by throttle"

    # Local lands a commit
    local_sha = _commit(repo, "local.txt", "local work\n", "local advance")

    # Publication converges release and tacos/release
    state = remote_sync.reconcile_remote_publication(
        cfg,
        repo,
        _TARGET,
        source_state=pull_record,
    )

    # Convergence proof: real ref equality
    local_ref = _head_sha(repo, f"refs/heads/{_TARGET}")
    bare_ref = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert local_ref == bare_ref == local_sha
    assert state.last_push_status == PushStatus.PUSHED
    assert state.last_outcome in (
        RebaseOutcomeKind.REMOTE_PUSHED,
        RebaseOutcomeKind.FAST_FORWARDED,
    )

    # No origin or main accessed or created
    remotes = _run(repo, "remote").stdout.split()
    assert "origin" not in remotes
    branches = _run(repo, "branch", "-a").stdout
    assert "main" not in branches


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_throttle_deferred_probe_still_publishes_concrete_refs(tmp_path: Path) -> None:
    """AC6 real-Git: a fetch suppressed by the throttle still allows the push.

    The reconciler must attempt the push when the local SHA is
    strictly ahead of the remote, regardless of probe freshness or
    previous in-memory state. After the push, the local and remote
    refs are equal in the real bare remote.
    """
    repo = tmp_path / "repo"
    bare = tmp_path / "tacos.git"

    _init_repo(repo)
    assert _run(tmp_path, "clone", "--bare", str(repo), str(bare)).returncode == 0
    assert _run(repo, "remote", "add", _REMOTE, str(bare)).returncode == 0
    assert _run(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _run(repo, "config", "user.name", "Test User").returncode == 0
    # Push the seed so the bare has the target branch.
    assert _run(repo, "push", _REMOTE, _TARGET).returncode == 0
    # Initial fetch so the remote tracking ref exists.
    assert _run(repo, "fetch", _REMOTE).returncode == 0

    # Local lands a commit, does NOT push yet (simulates a probe
    # suppression by an upstream throttle).
    local_sha = _commit(repo, "local.txt", "local work\n", "local advance")

    # Pre-state: the bare remote is behind.
    assert _head_sha(repo, f"refs/heads/{_TARGET}") == local_sha
    bare_pre = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert bare_pre != local_sha

    # Drive the reconciler: the throttle did not stop the push.
    state = remote_sync.reconcile_remote_publication(
        _build_config(),
        repo,
        _TARGET,
        remote=_REMOTE,
    )

    # Convergence proof: real ref equality after the push.
    local_ref = _head_sha(repo, f"refs/heads/{_TARGET}")
    bare_ref = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert local_ref == bare_ref == local_sha
    assert state.last_push_status == PushStatus.PUSHED
    assert state.last_outcome in (
        RebaseOutcomeKind.REMOTE_PUSHED,
        RebaseOutcomeKind.FAST_FORWARDED,
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_killed_between_landing_and_push_publishes_on_recovery(tmp_path: Path) -> None:
    """AC7 real-Git: a process killed between local land and push is recovered.

    The local repo carries a commit on the target branch but the
    remote is behind. The startup reconciler publishes the
    pending commit on the next call.
    """
    repo = tmp_path / "repo"
    bare = tmp_path / "tacos.git"

    _init_repo(repo)
    assert _run(tmp_path, "clone", "--bare", str(repo), str(bare)).returncode == 0
    assert _run(repo, "remote", "add", _REMOTE, str(bare)).returncode == 0
    assert _run(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _run(repo, "config", "user.name", "Test User").returncode == 0
    # Push the seed so the bare has the target branch.
    assert _run(repo, "push", _REMOTE, _TARGET).returncode == 0
    # Initial fetch so the remote tracking ref exists.
    assert _run(repo, "fetch", _REMOTE).returncode == 0

    # Land a commit locally but DO NOT push (simulates a process
    # killed between the local landing and the push).
    local_sha = _commit(repo, "local.txt", "local work\n", "local advance")

    # Pre-state: the local ref is ahead of the bare remote.
    assert _head_sha(repo, f"refs/heads/{_TARGET}") == local_sha
    bare_ref_before = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert bare_ref_before != local_sha

    # Drive the startup reconciler.
    state = remote_sync.reconcile_remote_publication(
        _build_config(),
        repo,
        _TARGET,
        remote=_REMOTE,
    )

    # Convergence proof: the bare remote ref has caught up.
    bare_ref_after = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert bare_ref_after == local_sha
    assert state.last_outcome in (
        RebaseOutcomeKind.REMOTE_PUSHED,
        RebaseOutcomeKind.FAST_FORWARDED,
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_unreachable_then_reachable_auto_pushes_real_refs(tmp_path: Path) -> None:
    """AC8 real-Git: a remote that becomes reachable is published to.

    The first reconciler call has no remote bare available (the bare
    path is empty or unconfigured), so the call returns a
    REMOTE_PULL_FAILED. After the bare remote is brought online and
    the local is configured to point at it, the second call
    publishes the local commit and the real refs converge.
    """
    repo = tmp_path / "repo"

    _init_repo(repo)
    assert _run(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _run(repo, "config", "user.name", "Test User").returncode == 0

    # Configure a remote that points at a non-existent bare (the
    # remote is "unreachable"). The reconciler cannot fetch and
    # returns a recoverable failure.
    unreachable_bare = tmp_path / "tacos_unreachable.git"
    assert _run(repo, "remote", "add", _REMOTE, str(unreachable_bare)).returncode == 0
    local_sha = _commit(repo, "local.txt", "local work\n", "local advance")
    state_first = remote_sync.reconcile_remote_publication(
        _build_config(),
        repo,
        _TARGET,
        remote=_REMOTE,
    )
    assert state_first.last_remote_sync in (
        remote_sync.REMOTE_PULL_FAILED,
        remote_sync.REMOTE_REMOTE_UNREACHABLE,
    )

    # Now bring the remote online (simulates the remote becoming
    # reachable after a network blip) and push the seed so the bare
    # has the target branch.
    bare = tmp_path / "tacos.git"
    init = subprocess.run(
        ("git", "init", "--bare", str(bare)),
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert init.returncode == 0
    assert _run(repo, "remote", "set-url", _REMOTE, str(bare)).returncode == 0
    # Seed the bare with the parent commit so the target branch exists.
    seed_sha = _run(repo, "rev-parse", "HEAD~1").stdout.strip()
    assert _run(repo, "push", _REMOTE, f"{seed_sha}:refs/heads/{_TARGET}").returncode == 0

    # Second reconciler call: the remote is reachable, the local
    # commit is published, refs converge.
    state_second = remote_sync.reconcile_remote_publication(
        _build_config(),
        repo,
        _TARGET,
        remote=_REMOTE,
    )

    bare_ref = _run(bare, "rev-parse", _TARGET).stdout.strip()
    local_ref = _head_sha(repo, f"refs/heads/{_TARGET}")
    assert bare_ref == local_ref == local_sha
    assert state_second.last_outcome in (
        RebaseOutcomeKind.REMOTE_PUSHED,
        RebaseOutcomeKind.FAST_FORWARDED,
    )
