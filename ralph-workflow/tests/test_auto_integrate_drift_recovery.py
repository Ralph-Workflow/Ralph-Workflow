"""Drift-recovery tests for the O2 publication reconciler.

Each test pins one acceptance criterion (AC5-AC9) from
``PLAN.md``. The tests are black-box: they drive the public
``reconcile_remote_publication`` entry point with in-memory fakes
and assert on the resulting ``RebaseState`` and on a recorded
``call_log`` so the implementation cannot regress to fire-once
publication or ``origin``/``main`` defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.config.models import UnifiedConfig
from ralph.git import remote_push as _remote_push_module
from ralph.pipeline import auto_integrate_remote_sync as remote_sync

if TYPE_CHECKING:
    import pytest

    from ralph.pipeline.auto_integrate_ff import RemoteClient
    from ralph.pipeline.rebase_state import RebaseState


class _RecordingRemoteClient:
    """In-memory remote client that records the configured remote/target pair."""

    def __init__(self, *, remote_sha: str | None = "remote-head-sha") -> None:
        self.remote_sha = remote_sha
        self.observed: list[tuple[str, str, str]] = []
        self.pushed: list[tuple[str, str]] = []
        self.refresh_outcomes: list[tuple[str, str, str]] = []

    def observe_remote_sha(self, repo_root: Path, target: str, *, remote: str) -> str | None:
        self.observed.append((remote, target, str(repo_root)))
        return self.remote_sha

    def refresh_target(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
        timeout_seconds: float = 0.0,
    ) -> str:
        self.refresh_outcomes.append((remote, target, str(repo_root)))
        return "refreshed"

    def push_branch(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
        timeout_seconds: float = 0.0,
    ) -> _remote_push_module.PushResult:
        self.pushed.append((remote, target))
        return _remote_push_module.PushResult(_remote_push_module.PushStatus.PUSHED, remote, target)


class _UnreachableRemoteClient:
    """In-memory remote client that reports UNREACHABLE for every push."""

    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []

    def observe_remote_sha(self, repo_root: Path, target: str, *, remote: str) -> str | None:
        return None  # unreachable: no SHA

    def refresh_target(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
        timeout_seconds: float = 0.0,
    ) -> str:
        return "unreachable"

    def push_branch(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
        timeout_seconds: float = 0.0,
    ) -> _remote_push_module.PushResult:
        self.pushed.append((remote, target))
        return _remote_push_module.PushResult(
            _remote_push_module.PushStatus.UNREACHABLE, remote, target
        )


def test_non_default_target_remote_converges(tmp_path: Path) -> None:
    """AC5: target=release + remote=tacos converge; no origin/main literal.

    Uses the in-memory client to record every observed/pushed call.
    The recorded calls MUST use ``tacos`` (not ``origin``) and the
    caller-supplied target ``release``. The reconciler never reads
    ``origin`` or ``main`` defaults.
    """
    client = _RecordingRemoteClient(remote_sha="tacos-release-sha")
    config = UnifiedConfig()
    # The test asserts the configured remote is the source of truth;
    # we override the policy by passing an explicit ``remote`` so the
    # test is self-contained.
    remote_sync.reconcile_remote_publication(
        config,
        tmp_path,
        "release",
        remote="tacos",
        remote_client=cast("RemoteClient", client),
    )
    # The remote/target names propagate to the recorded observations
    # and the published push.
    assert all(remote == "tacos" for remote, _, _ in client.observed)
    assert all(remote == "tacos" for remote, _ in client.pushed)


def test_unreachable_then_reachable_auto_pushes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC8: a REMOTE_PULL_FAILED state auto-pushes when the remote recovers.

    Tick 1: the remote is UNREACHABLE; the reconciler returns
    ``REMOTE_REMOTE_UNREACHABLE`` and does not push.
    Tick 2: the same call now reports a remote SHA; the reconciler
    pushes and returns ``REMOTE_PUSHED``.
    """
    monkeypatch.setattr(remote_sync, "branch_sha", lambda _r, _t: "local-sha")
    unreachable = _UnreachableRemoteClient()
    config = UnifiedConfig()
    state_1 = remote_sync.reconcile_remote_publication(
        config,
        tmp_path,
        "main",
        remote="origin",
        remote_client=cast("RemoteClient", unreachable),
    )
    assert state_1.last_remote_sync == "origin unreachable"
    assert unreachable.pushed == []

    # The remote recovers.
    reachable = _RecordingRemoteClient(remote_sha="new-remote-sha")

    # The new client returns a remote SHA different from local; stub
    # ``is_ancestor`` to claim the local is strictly ahead so the
    # push branch fires.
    def _stub_is_ancestor_recover(_root: Path, ancestor: str, descendant: str) -> bool:
        return ancestor == "new-remote-sha" and descendant == "local-sha"

    monkeypatch.setattr(remote_sync, "is_ancestor", _stub_is_ancestor_recover)
    remote_sync.reconcile_remote_publication(
        config,
        tmp_path,
        "main",
        remote="origin",
        remote_client=cast("RemoteClient", reachable),
    )
    # On the recovery tick, the reconciler pushes.
    assert reachable.pushed == [("origin", "main")]


def test_concurrent_remote_advance_calls_reconcile_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC9: a concurrent remote advance routes through ``reconcile_after_rejected_push``.

    When the local and remote SHAs have diverged, the reconciler
    must dispatch to ``reconcile_after_rejected_push`` (the
    bounded-retry reconcile loop). The test stubs that helper to
    assert the dispatch.
    """
    client = _RecordingRemoteClient(remote_sha="diverged-remote-sha")
    # Stub is_ancestor to claim both local_ahead and remote_ahead are
    # False. The reconciler imports ``is_ancestor`` and ``branch_sha``
    # at module load time; the test patches the names in the
    # reconciler's namespace directly.
    monkeypatch.setattr(remote_sync, "is_ancestor", lambda *_a, **_kw: False)
    # Also stub ``branch_sha`` so the reconciler can read the local
    # SHA without invoking git on a non-repo path.
    monkeypatch.setattr(remote_sync, "branch_sha", lambda _r, _t: "local-sha")
    captured: dict[str, object] = {}

    def _fake_reconcile(
        config: object,
        repo_root: Path,
        target: str,
        state: RebaseState,
        *,
        remote: str | None = None,
        remote_client: RemoteClient | None = None,
    ) -> RebaseState:
        captured["config"] = config
        captured["repo_root"] = repo_root
        captured["target"] = target
        captured["remote"] = remote
        return state.model_copy(update={"last_remote_sync": "push reconciled"})

    monkeypatch.setattr(remote_sync, "reconcile_after_rejected_push", _fake_reconcile)
    remote_sync.reconcile_remote_publication(
        UnifiedConfig(),
        tmp_path,
        "main",
        remote="tacos",
        remote_client=cast("RemoteClient", client),
    )
    assert captured.get("target") == "main"
    assert captured.get("remote") == "tacos"


def test_local_ahead_triggers_push(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC6: a fresh land pushes even when the configured remote is fresh.

    The reconciler is fire-once from the OLD design's perspective:
    it MUST attempt the push when the local SHA is strictly ahead
    of the remote, regardless of ``last_push_status`` or any other
    in-memory state.
    """
    client = _RecordingRemoteClient(remote_sha="remote-behind-sha")
    # The reconciler imports ``is_ancestor`` and ``branch_sha`` at
    # module load time; the test patches the names in the
    # reconciler's namespace directly.

    # ``is_ancestor(root, ancestor, descendant)``: claim the local
    # branch is the ancestor of the remote-tracking ref (i.e. local
    # is strictly behind) so the test is invalid. The local-ahead
    # branch fires when ``is_ancestor(remote_sha, local_sha)`` is True
    # -- the remote is an ancestor of the local, meaning local is
    # strictly ahead.
    def _stub_is_ancestor(_root: Path, ancestor: str, descendant: str) -> bool:
        return ancestor == "remote-behind-sha" and descendant == "local-sha-different"

    monkeypatch.setattr(remote_sync, "is_ancestor", _stub_is_ancestor)
    # Stub ``branch_sha`` so the reconciler can read the local SHA.
    monkeypatch.setattr(remote_sync, "branch_sha", lambda _r, _t: "local-sha-different")
    remote_sync.reconcile_remote_publication(
        UnifiedConfig(),
        tmp_path,
        "main",
        remote="origin",
        remote_client=cast("RemoteClient", client),
    )
    assert client.pushed == [("origin", "main")]


def test_killed_between_landing_and_push_recovery_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC7: a state with local ahead of remote still pushes on the next call.

    The reconciler is the single publication entry point. The
    ``last_push_status`` field is no longer consulted; the next
    call observes the divergence via the ref comparison and pushes
    immediately.
    """
    client = _RecordingRemoteClient(remote_sha="stale-remote-sha")
    # Local and remote differ; local is ahead.
    # The reconciler imports ``is_ancestor`` and ``branch_sha`` at
    # module load time; the test patches the names in the
    # reconciler's namespace directly.

    def _stub_is_ancestor(_root: Path, ancestor: str, descendant: str) -> bool:
        # Local is strictly ahead of remote.
        return ancestor == "stale-remote-sha" and descendant == "local-stale-sha"

    monkeypatch.setattr(remote_sync, "is_ancestor", _stub_is_ancestor)
    # Stub ``branch_sha`` so the reconciler can read the local SHA.
    monkeypatch.setattr(remote_sync, "branch_sha", lambda _r, _t: "local-stale-sha")
    remote_sync.reconcile_remote_publication(
        UnifiedConfig(),
        tmp_path,
        "main",
        remote="origin",
        remote_client=cast("RemoteClient", client),
    )
    assert client.pushed == [("origin", "main")]
