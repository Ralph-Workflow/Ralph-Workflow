from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate_boundary as boundary
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._publication_state_machine import PublicationDecision
from ralph.pipeline.auto_integrate_remote_sync import (
    REMOTE_ALREADY_CURRENT,
    REMOTE_PULL_FAILED,
    REMOTE_REMOTE_UNREACHABLE,
)

if TYPE_CHECKING:
    import pytest

from ralph.pipeline.rebase_state import RebaseState


def _make_config(
    *,
    target: str = "main",
    remote: str | None = None,
) -> UnifiedConfig:
    payload: dict[str, object] = {
        "general": {
            "auto_integrate_enabled": True,
            "auto_integrate_target": target,
        }
    }
    if remote is not None:
        payload["general"]["auto_integrate_remote"] = remote
    return UnifiedConfig.model_validate(payload)


def _stub_boundary_collaborators(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pending: RebaseState,
    head_sha: str = "stub-head-sha",
    unsafe_pull_record: RebaseState | None = None,
) -> list[str]:
    reconciler_targets: list[str] = []

    def fake_pull_and_reconcile(config, root, target, *, rebase_stop_resolver, prior):
        return unsafe_pull_record

    def fake_reconcile_remote_publication(config, root, target, *, rebase_stop_resolver):
        reconciler_targets.append(target)
        return pending

    monkeypatch.setattr(boundary, "pull_and_reconcile_target", fake_pull_and_reconcile)
    monkeypatch.setattr(
        boundary, "_reconcile_remote_publication", fake_reconcile_remote_publication
    )
    monkeypatch.setattr(boundary, "branch_sha", lambda r, t: head_sha)
    monkeypatch.setattr(boundary, "get_head_sha", lambda r: head_sha)
    monkeypatch.setattr(boundary, "remote_sync_enabled", lambda cfg: True)
    return reconciler_targets


def test_freshness_safe_false_does_not_block_reconciler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head_sha = "stub-head-sha"
    pending = RebaseState(last_remote_sync=REMOTE_ALREADY_CURRENT)
    unsafe_pull_record = RebaseState(freshness_safe=False)
    reconciler_targets = _stub_boundary_collaborators(
        monkeypatch,
        pending=pending,
        head_sha=head_sha,
        unsafe_pull_record=unsafe_pull_record,
    )

    result = boundary.boundary_freshness_outcome(
        _make_config(),
        tmp_path,
        "main",
        RebaseState(),
        rebase_stop_resolver=None,
    )

    assert reconciler_targets == ["main"], (
        "_reconcile_remote_publication was not called with the configured "
        "target — the freshness_safe early-return (Bug A) is still active"
    )
    assert result is not None


def test_transient_failure_preserves_reconciler_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending_record = RebaseState(
        last_remote_sync=REMOTE_REMOTE_UNREACHABLE,
        last_reason="remote unreachable during pull",
        last_publication_decision=PublicationDecision.PUBLISH_DEFERRED_REMOTE,
    )
    _stub_boundary_collaborators(monkeypatch, pending=pending_record)

    result = boundary.boundary_freshness_outcome(
        _make_config(),
        tmp_path,
        "main",
        RebaseState(),
        rebase_stop_resolver=None,
    )

    assert result is pending_record, (
        "transient failure verdict was overwritten: "
        f"expected last_remote_sync={REMOTE_REMOTE_UNREACHABLE!r}, "
        f"got {getattr(result, 'last_remote_sync', None)!r}"
    )
    assert result.last_remote_sync == REMOTE_REMOTE_UNREACHABLE
    assert result.last_reason != "no commits beyond target", (
        "last_reason was overwritten to 'no commits beyond target' — "
        "Bug B (transient-failure overwrite) is still present"
    )


def test_transient_failure_preserves_publish_deferred_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending_record = RebaseState(
        last_remote_sync=REMOTE_REMOTE_UNREACHABLE,
        last_reason="remote tacos unreachable; recovery will retry",
        last_publication_decision=PublicationDecision.PUBLISH_DEFERRED_REMOTE,
    )
    _stub_boundary_collaborators(monkeypatch, pending=pending_record)

    result = boundary.boundary_freshness_outcome(
        _make_config(target="release", remote="tacos"),
        tmp_path,
        "release",
        RebaseState(),
        rebase_stop_resolver=None,
    )

    assert result is not None
    assert result.last_remote_sync == REMOTE_REMOTE_UNREACHABLE
    assert result.last_publication_decision == PublicationDecision.PUBLISH_DEFERRED_REMOTE, (
        "PUBLISH_DEFERRED_REMOTE obligation was lost at the boundary: "
        "AC-8 requires the typed publication obligation be carried forward "
        "so the next seam retries the deferred push"
    )


def test_transient_failure_pull_failed_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending_record = RebaseState(
        last_remote_sync=REMOTE_PULL_FAILED,
        last_reason="pull failed: connection reset",
        last_publication_decision=PublicationDecision.PUBLISH_DEFERRED_REMOTE,
    )
    _stub_boundary_collaborators(monkeypatch, pending=pending_record)

    result = boundary.boundary_freshness_outcome(
        _make_config(),
        tmp_path,
        "main",
        RebaseState(),
        rebase_stop_resolver=None,
    )

    assert result is pending_record
    assert result.last_remote_sync == REMOTE_PULL_FAILED
    assert result.last_publication_decision == PublicationDecision.PUBLISH_DEFERRED_REMOTE


def test_already_current_returns_skip_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending_record = RebaseState(
        last_remote_sync=REMOTE_ALREADY_CURRENT,
        last_reason="local and remote already converge",
    )
    _stub_boundary_collaborators(monkeypatch, pending=pending_record)

    result = boundary.boundary_freshness_outcome(
        _make_config(),
        tmp_path,
        "main",
        RebaseState(),
        rebase_stop_resolver=None,
    )

    assert result is not None
    assert result.last_reason == "no commits beyond target"


def test_unsafe_divergence_propagates_reintegration_without_force_push(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending_record = RebaseState(
        last_outcome=RebaseOutcomeKind.REMOTE_FAILED,
        last_reason="push of release to tacos failed: non-fast-forward; "
        "reconciler delegated to reintegration",
        last_publication_decision=PublicationDecision.PUBLISH_DEFERRED_REMOTE,
    )
    _stub_boundary_collaborators(monkeypatch, pending=pending_record)

    result = boundary.boundary_freshness_outcome(
        _make_config(),
        tmp_path,
        "main",
        RebaseState(),
        rebase_stop_resolver=None,
    )

    assert result is not None
    assert result.last_reason != "no commits beyond target", (
        "divergence outcome was overwritten with the silent-skip; "
        "the divergence signal must reach the next seam"
    )
    assert result.last_outcome == RebaseOutcomeKind.REMOTE_FAILED
    assert result.last_publication_decision == PublicationDecision.PUBLISH_DEFERRED_REMOTE


def test_configured_remote_and_target_threaded_to_reconciler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head_sha = "stub-head-sha"
    pending = RebaseState(last_remote_sync=REMOTE_ALREADY_CURRENT)
    reconciler_targets = _stub_boundary_collaborators(
        monkeypatch, pending=pending, head_sha=head_sha
    )

    boundary.boundary_freshness_outcome(
        _make_config(target="release", remote="tacos"),
        tmp_path,
        "release",
        RebaseState(),
        rebase_stop_resolver=None,
    )

    assert reconciler_targets == ["release"], (
        "boundary did not thread the configured target to the reconciler; "
        f"expected ['release'], got {reconciler_targets}"
    )


def test_head_differs_from_target_gates_integration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reconcile_called = False

    def fake_pull_and_reconcile(config, root, target, *, rebase_stop_resolver, prior):
        return None

    def fake_reconcile_remote_publication(config, root, target, *, rebase_stop_resolver):
        nonlocal reconcile_called
        reconcile_called = True
        return RebaseState()

    monkeypatch.setattr(boundary, "pull_and_reconcile_target", fake_pull_and_reconcile)
    monkeypatch.setattr(
        boundary, "_reconcile_remote_publication", fake_reconcile_remote_publication
    )
    monkeypatch.setattr(boundary, "branch_sha", lambda r, t: "feature-sha")
    monkeypatch.setattr(boundary, "get_head_sha", lambda r: "main-sha")
    monkeypatch.setattr(boundary, "remote_sync_enabled", lambda cfg: False)

    result = boundary.boundary_freshness_outcome(
        _make_config(),
        tmp_path,
        "main",
        RebaseState(),
        rebase_stop_resolver=None,
    )

    assert result is None, (
        "boundary_freshness_outcome did not return None: "
        "the feature-integration gate did not fire when HEAD != target"
    )
    assert not reconcile_called, (
        "_reconcile_remote_publication was called despite HEAD != target: "
        "the feature-integration gate was bypassed"
    )


def test_unsafe_divergence_real_reconciler_uses_rejected_push_path_without_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ralph.pipeline import auto_integrate_remote_sync as remote_sync
    from ralph.pipeline._integration_action import RebaseOutcomeKind
    from ralph.pipeline._publication_state_machine import PublicationDecision

    forced_calls: list[tuple[Path, str]] = []
    rejected_calls: list[tuple[Path, str, str]] = []

    head_sha = "local-release-sha"

    def fake_branch_sha(_root: Path, _target: str) -> str:
        return head_sha

    def fake_local_sha(_root: Path, _target: str) -> str:
        return head_sha

    def fake_observe_remote_sha(
        _root: Path,
        _target: str,
        *,
        remote: str,
        remote_client: object = None,
        local_sha: str | None = None,
    ) -> tuple[str | None, str]:
        return "remote-advance-sha", "diverged"

    def fake_is_ancestor(_r: Path, _a: str, _d: str) -> bool:
        return _a == "remote-advance-sha"

    from ralph.git.remote_push import PushStatus

    def fake_push_target_after_landing(
        _config: object,
        _root: Path,
        _target: str,
        _base_state: object,
        *,
        remote: str,
        remote_client: object = None,
    ) -> RebaseState:
        forced_calls.append((_root, remote))
        return RebaseState(
            last_outcome=RebaseOutcomeKind.REMOTE_FAILED,
            last_remote_sync="rejected",
            last_push_status=PushStatus.NON_FAST_FORWARD.value,
            last_remote=remote,
            last_target=_target,
            last_publication_decision=PublicationDecision.PUBLISH_DEFERRED_REMOTE,
        )

    def fake_reconcile_after_rejected_push(
        _config: object,
        _root: Path,
        _target: str,
        _pushed: RebaseState,
        *,
        remote: str,
        rebase_stop_resolver: object = None,
        remote_client: object = None,
    ) -> RebaseState:
        rejected_calls.append((_root, _target, remote))
        return _pushed.model_copy(
            update={
                "last_reason": (
                    f"push of {_target} to {remote} failed: non-fast-forward; "
                    "reconciler delegated to reintegration"
                ),
                "last_publication_decision": PublicationDecision.PUBLISH_DEFERRED_REMOTE,
            }
        )

    monkeypatch.setattr(boundary, "branch_sha", fake_branch_sha)
    monkeypatch.setattr(boundary, "get_head_sha", lambda r: head_sha)
    monkeypatch.setattr(boundary, "remote_sync_enabled", lambda cfg: True)
    monkeypatch.setattr(remote_sync, "remote_sync_enabled", lambda cfg: True)
    monkeypatch.setattr(boundary, "pull_and_reconcile_target", lambda *_a, **_k: None)
    monkeypatch.setattr(remote_sync, "_safe_local_sha", fake_local_sha)
    monkeypatch.setattr(remote_sync, "_observe_remote_target_sha", fake_observe_remote_sha)
    monkeypatch.setattr(remote_sync, "is_ancestor", fake_is_ancestor)
    monkeypatch.setattr(remote_sync, "push_target_after_landing", fake_push_target_after_landing)
    monkeypatch.setattr(
        remote_sync,
        "reconcile_after_rejected_push",
        fake_reconcile_after_rejected_push,
    )

    result = boundary.boundary_freshness_outcome(
        _make_config(target="release", remote="tacos"),
        tmp_path,
        "release",
        RebaseState(),
        rebase_stop_resolver=None,
    )

    assert len(forced_calls) == 1, (
        f"Boundary must consult the real reconciler on divergence; forced_calls={forced_calls}"
    )
    assert forced_calls[0][1] == "tacos", (
        f"Push must target the configured remote 'tacos'; got {forced_calls[0][1]}"
    )
    assert len(rejected_calls) == 1, (
        f"Boundary must delegate divergence to reconcile_after_rejected_push; "
        f"rejected_calls={rejected_calls}"
    )
    assert result is not None
    assert result.last_reason != "no commits beyond target", (
        "divergence outcome was overwritten with the silent-skip; "
        "the divergence signal must reach the next seam"
    )
    assert result.last_publication_decision == PublicationDecision.PUBLISH_DEFERRED_REMOTE, (
        "The rejected-push path must carry forward PUBLISH_DEFERRED_REMOTE "
        "so the next seam retries the deferred push"
    )
    assert result.last_remote == "tacos", (
        "The configured remote name must survive the divergence path; "
        "no hard-coded 'origin' default may appear"
    )
