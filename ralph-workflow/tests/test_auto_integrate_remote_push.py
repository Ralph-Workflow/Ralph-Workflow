"""Deterministic wrapper coverage for opt-in auto-integration publication."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate_ff
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    import pytest

    from ralph.git import remote_push as _remote_push_module
    from ralph.pipeline.conflict_resolution import RebaseStop


def _config(*, enabled: bool) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_remote_enabled": enabled,
                "auto_integrate_remote": "origin",
            }
        }
    )


def _record() -> RebaseState:
    return RebaseState(
        last_outcome="rebased",
        last_target="release",
        fast_forwarded=True,
        freshness_verdict="verified",
    )


def test_enabled_remote_sync_delegates_landing_to_push_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public landing wrapper forwards its configured target to the push seam."""
    from ralph.git.remote_push import PushResult, PushStatus
    from ralph.pipeline._publication_state_machine import PublicationDecision
    from ralph.pipeline.auto_integrate_remote_sync import push_target_after_landing

    calls: list[tuple[Path, str, str | None, RebaseState | None]] = []
    record = _record()

    def push_hook(
        repo_root: Path,
        target: str,
        *,
        remote: str | None = None,
        record: RebaseState | None = None,
        remote_client: object = None,
        timeout_seconds: float | None = None,
    ) -> tuple[PushResult, PublicationDecision]:
        calls.append((repo_root, target, remote, record))
        return (
            PushResult(
                PushStatus.PUSHED,
                remote or "origin",
                target,
            ),
            PublicationDecision.PUBLICATION_TERMINAL,
        )

    monkeypatch.setattr("ralph.pipeline.auto_integrate_remote_sync.push_single_remote", push_hook)
    monkeypatch.setattr("ralph.pipeline.auto_integrate_remote_push.push_single_remote", push_hook)

    result = push_target_after_landing(_config(enabled=True), Path("/repo"), "release", record)

    assert result.last_push == "pushed release to origin"
    assert result.last_remote == "origin"
    assert result.last_push_status == "pushed"
    assert result.last_remote_sync == "pushed"
    assert result.last_publication_decision == "publication_terminal"
    assert len(calls) == 1
    assert calls[0][0] == Path("/repo")
    assert calls[0][1] == "release"
    assert calls[0][2] == "origin"
    assert calls[0][3] is not None
    assert calls[0][3].freshness_verdict == "verified"


def test_disabled_remote_sync_preserves_landing_without_calling_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public wrapper leaves a local landing untouched when sync is disabled."""
    from unittest.mock import MagicMock

    from ralph.pipeline.auto_integrate_remote_sync import push_target_after_landing

    record = _record()

    def unexpected_hook(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("disabled remote sync called its push hook")

    recording_double = MagicMock(side_effect=unexpected_hook)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync.push_single_remote", recording_double
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_push.push_single_remote", recording_double
    )

    result = push_target_after_landing(_config(enabled=False), Path("/repo"), "release", record)

    assert result.fast_forwarded is True
    assert result.last_publication_decision == "publication_terminal"
    assert recording_double.call_count == 0


def test_push_hook_exception_preserves_local_landing(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unexpected remote-hook failure never rolls back a successful local landing."""
    from ralph.git.remote_push import PushResult, PushStatus
    from ralph.pipeline._publication_state_machine import PublicationDecision

    record = _record()

    def failing_hook(*_args: object, **_kwargs: object) -> tuple[PushResult, PublicationDecision]:
        return (
            PushResult(
                PushStatus.UNREACHABLE,
                "origin",
                "release",
                detail="remote unavailable",
            ),
            PublicationDecision.PUBLISH_DEFERRED_REMOTE,
        )

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync.push_single_remote", failing_hook
    )

    from ralph.pipeline.auto_integrate_remote_sync import push_target_after_landing

    result = push_target_after_landing(_config(enabled=True), Path("/repo"), "release", record)
    assert result.fast_forwarded is True
    assert result.last_publication_decision == "publish_deferred_remote"
    assert result.last_remote_sync == "origin unreachable"
    assert result.last_push_status == "unreachable"
    assert "remote unavailable" in (result.last_reason or "")


class _ExceptionRaisingRemoteClient(auto_integrate_ff.RemoteClient):
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def observe_remote_sha(self, repo_root: Path, target: str, *, remote: str) -> str | None:
        return "remote-sha"

    def refresh_target(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
        timeout_seconds: float = 0.0,
    ) -> str:
        return "refreshed"

    def push_branch(
        self,
        repo_root: Path,
        branch: str,
        *,
        remote: str,
        timeout_seconds: float = 0.0,
    ) -> _remote_push_module.PushResult:
        raise self._exc


def test_push_single_remote_catches_push_branch_exception_and_projects_unreachable() -> None:
    """Unexpected push_branch exception is caught and projected to UNREACHABLE without escaping."""
    from ralph.git.remote_push import PushStatus
    from ralph.pipeline._publication_state_machine import PublicationDecision
    from ralph.pipeline.auto_integrate_remote_push import push_single_remote

    client = _ExceptionRaisingRemoteClient(RuntimeError("connection reset by peer"))
    record = _record()

    typed_result, decision = push_single_remote(
        Path("/repo"),
        "release",
        remote="tacos",
        record=record,
        remote_client=client,
    )

    assert typed_result is not None
    assert typed_result.status == PushStatus.UNREACHABLE
    assert typed_result.remote == "tacos"
    assert typed_result.branch == "release"
    assert "connection reset by peer" in typed_result.detail
    assert decision == PublicationDecision.PUBLISH_DEFERRED_REMOTE


def test_push_single_remote_catches_timeout_and_projects_timeout() -> None:
    """Timeout during push_branch is caught and projected to TIMEOUT without escaping."""
    from ralph.git.remote_push import PushStatus
    from ralph.pipeline._publication_state_machine import PublicationDecision
    from ralph.pipeline.auto_integrate_remote_push import push_single_remote

    client = _ExceptionRaisingRemoteClient(TimeoutError("push timed out"))
    record = _record()

    typed_result, decision = push_single_remote(
        Path("/repo"),
        "release",
        remote="tacos",
        record=record,
        remote_client=client,
    )

    assert typed_result is not None
    assert typed_result.status == PushStatus.TIMEOUT
    assert typed_result.remote == "tacos"
    assert typed_result.branch == "release"
    assert decision == PublicationDecision.PUBLISH_DEFERRED_REMOTE


def test_push_target_after_landing_handles_push_branch_exception_without_losing_landing() -> None:
    """AC5, AC8: push_target_after_landing catches unexpected push exceptions and preserves landing."""
    from ralph.config.models import GeneralConfig
    from ralph.pipeline._integration_action import RebaseOutcomeKind
    from ralph.pipeline.auto_integrate_remote_push import push_target_after_landing

    config = UnifiedConfig(
        general=GeneralConfig(
            auto_integrate_enabled=True,
            auto_integrate_target="release",
            auto_integrate_remote="tacos",
            auto_integrate_remote_enabled=True,
        )
    )
    client = _ExceptionRaisingRemoteClient(RuntimeError("fatal: remote end hung up"))
    record = _record()

    result = push_target_after_landing(
        config,
        Path("/repo"),
        "release",
        record,
        remote="tacos",
        remote_client=client,
    )

    assert result.fast_forwarded is True
    assert result.last_push_status == "unreachable"
    assert result.last_publication_decision == "publish_deferred_remote"
    assert result.last_outcome == RebaseOutcomeKind.REMOTE_FAILED
    assert result.last_remote == "tacos"
    assert "hung up" in (result.last_reason or "")


def test_retry_pending_remote_publish_forwards_resolver_and_preserves_landing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC5, AC9: retry_pending_remote_publish forwards rebase_stop_resolver and preserves landing."""
    resolver_called = False

    def dummy_resolver(root: Path, target: str, stop: RebaseStop) -> bool:
        nonlocal resolver_called
        resolver_called = True
        return True

    captured_kwargs: dict[str, object] = {}

    def fake_reconcile(
        config: UnifiedConfig | None,
        repo_root: Path,
        target: str,
        *,
        remote: str | None = None,
        remote_client: object = None,
        rebase_stop_resolver: object = None,
        source_state: RebaseState | None = None,
    ) -> RebaseState:
        captured_kwargs["rebase_stop_resolver"] = rebase_stop_resolver
        captured_kwargs["source_state"] = source_state
        captured_kwargs["remote"] = remote
        assert source_state is not None
        return source_state.model_copy(update={"last_push_status": "pushed"})

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync.reconcile_remote_publication",
        fake_reconcile,
    )

    config = _config(enabled=True)
    record = _record().model_copy(update={"last_remote": "tacos"})

    res = auto_integrate_ff.retry_pending_remote_publish(
        config,
        Path("/repo"),
        "release",
        record,
        rebase_stop_resolver=dummy_resolver,
    )

    assert res is not None
    assert res.fast_forwarded is True
    assert captured_kwargs["rebase_stop_resolver"] is dummy_resolver
    assert captured_kwargs["source_state"] is record
    assert captured_kwargs["remote"] == "tacos"
