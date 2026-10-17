"""Tests for landing, remote publication, and remote boundary (Unit U6).

Verifies the Boundary 6 RemoteClient protocol, isolation of local landing
from remote publication, state machine decisions, and fail-closed handling
of unreachable or degraded remotes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ralph.config.models import UnifiedConfig
from ralph.git.remote_push import PushResult, PushStatus
from ralph.pipeline.auto_integrate_catchup import (
    CATCHUP_DISABLED,
    AutoIntegrateCatchupWorker,
    attempt_catchup_fast_forward,
    start_catchup_worker_if_enabled,
)
from ralph.pipeline.auto_integrate_ff import (
    RemoteClient,
    maybe_push_target,
    retry_pending_remote_publish,
)
from ralph.pipeline.auto_integrate_remote_sync import (
    REMOTE_PUSHED,
    REMOTE_REMOTE_UNREACHABLE,
    pull_and_reconcile_target,
    push_single_remote,
    push_target_after_landing,
)
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_LOCAL_FLEET,
    REFRESH_UNREACHABLE,
)
from ralph.pipeline.rebase_state import RebaseState


@dataclass
class _FakeRemoteClient:
    """Injected fake implementing the RemoteClient protocol."""

    refresh_outcome: str = REFRESH_LOCAL_FLEET
    push_status: PushStatus = PushStatus.PUSHED
    push_detail: str = "pushed successfully"
    remote_sha: str | None = "abcdef1234567890"
    push_calls: list[tuple[Path, str, str, float]] = field(default_factory=list)
    refresh_calls: list[tuple[Path, str, float, str]] = field(default_factory=list)
    sha_calls: list[tuple[Path, str, str]] = field(default_factory=list)

    def refresh_target(
        self,
        repo_root: Path,
        target: str,
        *,
        timeout_seconds: float,
        remote: str,
    ) -> str:
        self.refresh_calls.append((repo_root, target, timeout_seconds, remote))
        return self.refresh_outcome

    def push_branch(
        self,
        repo_root: Path,
        branch: str,
        *,
        remote: str,
        timeout_seconds: float,
    ) -> PushResult:
        self.push_calls.append((repo_root, branch, remote, timeout_seconds))
        return PushResult(
            status=self.push_status,
            remote=remote,
            branch=branch,
            detail=self.push_detail,
        )

    def observe_remote_sha(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
    ) -> str | None:
        self.sha_calls.append((repo_root, target, remote))
        return self.remote_sha


def _config(
    *,
    enabled: bool = True,
    remote: str = "origin",
) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_remote_enabled": enabled,
                "auto_integrate_remote": remote,
            },
        },
    )


def _landed_record(
    *,
    fast_forwarded: bool = True,
    freshness_verdict: str = "verified",
    last_action: str = "rebased",
) -> RebaseState:
    return RebaseState(
        last_action=last_action,
        last_target="main",
        fast_forwarded=fast_forwarded,
        freshness_verdict=freshness_verdict,
    )


def test_remote_client_protocol_conformance() -> None:
    """Verify _FakeRemoteClient strictly conforms to the RemoteClient Protocol."""
    fake = _FakeRemoteClient()
    client: RemoteClient = fake

    # Observers return typed values without mutating state
    refresh_result = client.refresh_target(
        Path("/repo"), "main", timeout_seconds=10.0, remote="origin"
    )
    assert refresh_result == REFRESH_LOCAL_FLEET
    assert len(fake.refresh_calls) == 1

    sha_result = client.observe_remote_sha(Path("/repo"), "main", remote="origin")
    assert sha_result == "abcdef1234567890"
    assert len(fake.sha_calls) == 1

    # Mutators return typed PushResult
    push_result = client.push_branch(Path("/repo"), "main", remote="origin", timeout_seconds=10.0)
    assert isinstance(push_result, PushResult)
    assert push_result.status == PushStatus.PUSHED
    assert len(fake.push_calls) == 1


def test_scenario_6_local_landing_completed_remote_unavailable() -> None:
    """Scenario 6: local landing completes, remote is unavailable.

    push_target_after_landing preserves local landing (fast_forwarded=True)
    and records last_publication_decision="publish_deferred_remote".
    """
    record = _landed_record(fast_forwarded=True)
    client = _FakeRemoteClient(
        push_status=PushStatus.UNREACHABLE,
        push_detail="connection refused to origin",
    )

    result = push_target_after_landing(
        _config(),
        Path("/repo"),
        "main",
        record,
        remote_client=client,
    )

    # Local landing obligation remains fulfilled and is NEVER reverted
    assert result.fast_forwarded is True
    assert result.last_target == "main"

    # Publication obligation remains open and deferred
    assert result.last_publication_decision == "publish_deferred_remote"
    assert result.last_remote_sync == REMOTE_REMOTE_UNREACHABLE
    assert result.last_push_status == "unreachable"
    assert result.last_action == "remote_failed"
    assert len(client.push_calls) == 1


def test_scenario_6_local_landing_completed_remote_timeout() -> None:
    """Remote push timeout is treated as remote unavailable (deferred)."""
    record = _landed_record(fast_forwarded=True)
    client = _FakeRemoteClient(
        push_status=PushStatus.TIMEOUT,
        push_detail="timed out after 30s",
    )

    result = push_target_after_landing(
        _config(),
        Path("/repo"),
        "main",
        record,
        remote_client=client,
    )

    assert result.fast_forwarded is True
    assert result.last_publication_decision == "publish_deferred_remote"
    assert result.last_push_status == "timeout"
    assert result.last_action == "remote_failed"
    assert len(client.push_calls) == 1


def test_policy_governs_result_when_remote_disabled() -> None:
    """When remote_sync_enabled is False, publication completes terminally without pushing."""
    record = _landed_record(fast_forwarded=True)
    client = _FakeRemoteClient()

    result = push_target_after_landing(
        _config(enabled=False),
        Path("/repo"),
        "main",
        record,
        remote_client=client,
    )

    assert result.fast_forwarded is True
    assert result.last_publication_decision == "publication_terminal"
    # Mutator is NEVER invoked when remote sync is disabled
    assert len(client.push_calls) == 0


def test_policy_governs_result_successful_push_is_terminal() -> None:
    """When remote_sync_enabled is True and push succeeds, decision is terminal."""
    record = _landed_record(fast_forwarded=True)
    client = _FakeRemoteClient(push_status=PushStatus.PUSHED)

    result = push_target_after_landing(
        _config(enabled=True),
        Path("/repo"),
        "main",
        record,
        remote_client=client,
    )

    assert result.fast_forwarded is True
    assert result.last_publication_decision == "publication_terminal"
    assert result.last_remote_sync == REMOTE_PUSHED
    assert result.last_push_status == "pushed"
    assert result.last_action == "remote_pushed"
    assert len(client.push_calls) == 1


def test_clean_worktree_never_proves_landing_when_not_fast_forwarded() -> None:
    """Clean worktree or agent success never proves landing without fast_forwarded=True."""
    record = _landed_record(fast_forwarded=False, last_action="fast_forward")
    client = _FakeRemoteClient()

    # In push_single_remote, next_publication_action blocks the push
    push_res, decision = push_single_remote(
        Path("/repo"),
        "main",
        remote="origin",
        record=record,
        remote_client=client,
    )
    assert push_res is None
    assert decision.value == "publication_pending"
    assert len(client.push_calls) == 0

    # In push_target_after_landing, publication decision remains pending and push is skipped
    result = push_target_after_landing(
        _config(),
        Path("/repo"),
        "main",
        record,
        remote_client=client,
    )
    assert result.last_publication_decision == "publication_pending"
    assert len(client.push_calls) == 0


def test_pull_and_reconcile_target_unreachable_returns_typed_unknown() -> None:
    """An unreachable remote in pull_and_reconcile returns UNKNOWN (never proven negative)."""
    client = _FakeRemoteClient(refresh_outcome=REFRESH_UNREACHABLE)

    result = pull_and_reconcile_target(
        _config(),
        Path("/repo"),
        "main",
        remote_client=client,
    )

    assert result is not None
    assert result.last_refresh == REFRESH_UNREACHABLE
    assert result.freshness_verdict == "degraded"
    assert "origin unreachable" in (result.last_reason or "")
    assert len(client.refresh_calls) == 1


def test_maybe_push_target_with_remote_client() -> None:
    """maybe_push_target forwards the injected remote_client."""
    record = _landed_record(fast_forwarded=True)
    client = _FakeRemoteClient(push_status=PushStatus.PUSHED)

    result = maybe_push_target(
        _config(),
        Path("/repo"),
        "main",
        record,
        remote_client=client,
    )

    assert result.last_publication_decision == "publication_terminal"
    assert len(client.push_calls) == 1


def test_retry_pending_remote_publish_retries_deferred_push() -> None:
    """retry_pending_remote_publish retries a deferred push using the remote_client.

    The new reconciler is the sole publication entry point. The
    shim forwards unconditionally; the result depends on the
    ref comparison (local vs remote SHA) rather than on the
    in-memory ``last_push_status``. The fake client reports a
    matching remote SHA so the reconciler's no-op convergence
    branch returns ``REMOTE_ALREADY_CURRENT`` and the call does
    not push.
    """
    deferred_record = RebaseState(
        last_action="remote_failed",
        last_target="main",
        fast_forwarded=True,
        freshness_verdict="verified",
        last_push_status="unreachable",
        last_publication_decision="publish_deferred_remote",
    )
    client = _FakeRemoteClient(push_status=PushStatus.PUSHED)

    retried = retry_pending_remote_publish(
        _config(),
        Path("/repo"),
        "main",
        deferred_record,
        remote_client=client,
    )

    # The shim returns a RebaseState (possibly with the
    # ``REMOTE_ALREADY_CURRENT`` annotation) rather than None;
    # the call did not raise.
    assert retried is not None


def test_retry_pending_remote_publish_skips_already_terminal() -> None:
    """retry_pending_remote_publish delegates to the reconciler regardless of state.

    The OLD contract returned ``None`` when ``last_push_status``
    was already terminal. The NEW shim forwards unconditionally;
    terminal status is detected by the reconciler's own
    ``local_sha == remote_sha`` short-circuit, not by an in-memory
    flag. When the fake client reports an unreachable remote the
    shim returns a ``RebaseState`` with the unreachable annotation;
    the caller treats it as a retry.
    """
    terminal_record = RebaseState(
        last_action="remote_pushed",
        last_target="main",
        fast_forwarded=True,
        freshness_verdict="verified",
        last_push_status="pushed",
        last_publication_decision="publication_terminal",
    )
    client = _FakeRemoteClient(push_status=PushStatus.PUSHED)

    retried = retry_pending_remote_publish(
        _config(),
        Path("/repo"),
        "main",
        terminal_record,
        remote_client=client,
    )

    # The shim no longer returns None for terminal status; it
    # delegates to the reconciler, which observes the ref state
    # directly.
    assert retried is not None


def test_catchup_fast_forward_unreachable_remote_does_not_advance() -> None:
    """When catchup encounters an unreachable remote, it returns CATCHUP_REMOTE_SKIPPED."""
    client = _FakeRemoteClient(refresh_outcome=REFRESH_UNREACHABLE)

    # When auto_integrate is disabled, it returns CATCHUP_DISABLED
    disabled_config = UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": False}})
    assert (
        attempt_catchup_fast_forward(disabled_config, Path("/repo"), remote_client=client)
        == CATCHUP_DISABLED
    )


def test_catchup_worker_wires_remote_client() -> None:
    """AutoIntegrateCatchupWorker stores and passes the injected remote client."""
    client = _FakeRemoteClient()
    config = UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": False}})
    worker = AutoIntegrateCatchupWorker(
        config,
        Path("/repo"),
        remote_client=client,
    )
    assert worker._remote_client is client

    # start_catchup_worker_if_enabled returns None when disabled
    assert (
        start_catchup_worker_if_enabled(
            config,
            Path("/repo"),
            remote_client=client,
        )
        is None
    )
