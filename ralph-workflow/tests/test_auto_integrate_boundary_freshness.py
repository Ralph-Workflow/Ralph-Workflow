"""Tests for boundary_freshness_outcome: Bug A (freshness_safe gate) and Bug B (transient-failure overwrite).

AC-6: boundary consults the publication reconciler regardless of ``freshness_safe``.
AC-8: boundary preserves the reconciler's transient-failure verdict AND its
      ``PublicationDecision.PUBLISH_DEFERRED_REMOTE`` obligation.
AC-9: configured target and remote names (e.g. ``release`` / ``tacos``) are
      propagated through the boundary seam; no hard-coded ``main``/``origin``
      defaults silently override configuration.
AC-12: unsafe divergence is handed back to the reconciler's reintegration path
       without force-pushing an unlanded feature SHA.
"""

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
    """Build a typed UnifiedConfig for boundary tests with optional target/remote.

    Defaults to the canonical ``main`` target; the configured ``tacos``
    remote is opt-in so AC-9 can assert propagation with non-default
    names. AC-9 cannot be inferred from the in-process boundary
    contract alone — the publication reconciler is the single owner
    of the configured remote name — but the boundary must thread
    the configured ``target`` and pass the configured ``config``
    object to the reconciler unchanged.
    """
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
) -> list[str]:
    """Stub the boundary's collaborators so the unit test exercises only the seam.

    Returns a list of ``reconciler_called_with_target`` entries so the test can
    assert the configured ``target`` was threaded through unchanged.
    """
    reconciler_targets: list[str] = []

    def fake_pull_and_reconcile(config, root, target, *, rebase_stop_resolver, prior):
        return None

    def fake_reconcile_remote_publication(config, root, target, *, rebase_stop_resolver):
        reconciler_targets.append(target)
        return pending

    monkeypatch.setattr(boundary, "pull_and_reconcile_target", fake_pull_and_reconcile)
    monkeypatch.setattr(
        boundary, "_reconcile_remote_publication", fake_reconcile_remote_publication
    )
    monkeypatch.setattr(boundary, "branch_sha", lambda r, t: head_sha)
    monkeypatch.setattr(boundary, "get_head_sha", lambda r: head_sha)
    # remote_sync_enabled is True so the boundary does not call
    # _refresh_target (which would touch real git). The boundary then
    # takes the reconciler's `pending` directly without consulting
    # record_when_stale. This keeps the unit test free of subprocess
    # I/O while still exercising the seam's verdict-surface logic.
    monkeypatch.setattr(boundary, "remote_sync_enabled", lambda cfg: True)
    return reconciler_targets


def test_freshness_safe_false_does_not_block_reconciler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-6: freshness_safe=False no longer gates the reconciler out.

    Before the fix, ``if remote_record is not None and not
    remote_record.freshness_safe: return remote_record`` short-circuited
    the boundary before ``_reconcile_remote_publication`` was ever
    called. After the fix the reconciler is always consulted.
    """
    head_sha = "stub-head-sha"
    pending = RebaseState(last_remote_sync=REMOTE_ALREADY_CURRENT)
    reconciler_targets = _stub_boundary_collaborators(
        monkeypatch, pending=pending, head_sha=head_sha
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
    # The function should return pending (the reconciler's record).
    assert result is not None


def test_transient_failure_preserves_reconciler_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-8: REMOTE_REMOTE_UNREACHABLE is propagated, not overwritten.

    Before the fix, ``if already_current or is_transient_failure:``
    fell through to the "no commits beyond target" skip path for both
    cases. After the fix, transient failures return ``pending``
    (the reconciler's record) so the unreachable signal is preserved.
    """
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
    """AC-8 obligation surface: PUBLISH_DEFERRED_REMOTE survives the boundary.

    The reconciler's transient-failure verdict carries both the prose
    signal (``REMOTE_REMOTE_UNREACHABLE``) and the typed obligation
    (``PublicationDecision.PUBLISH_DEFERRED_REMOTE``). O2 / AC-8 require
    that a pending push obligation is carried forward as the next seam's
    input, not silently dropped. The boundary must propagate the typed
    obligation alongside the prose reason.
    """
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
    """AC-8 variant: REMOTE_PULL_FAILED is also preserved."""
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
    """already_current path: last_reason is overwritten to 'no commits beyond target' (correct).

    The already_current branch is a deliberate silent skip: no push
    is owed, so overwriting the reconciler's prose is intentional. The
    transient-failure branch is the opposite — the unreachable signal
    MUST survive so the next seam retries it.
    """
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
    # The already_current path overwrites last_reason/last_action — this
    # is intentional and correct (no push needed, silent skip).
    assert result.last_reason == "no commits beyond target"


def test_unsafe_divergence_propagates_reintegration_without_force_push(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-12: divergence outcome is propagated; boundary never force-pushes.

    The reconciler's divergence path runs
    :func:`reconcile_after_rejected_push`, which performs reintegration
    without a forced refspec. The boundary must NOT overwrite that
    outcome with the "no commits beyond target" skip — that would
    hide a real divergence from the next caller. The boundary must
    also NOT publish an unlanded feature SHA: the
    ``branch_sha == get_head_sha`` precondition at
    ``auto_integrate_boundary.py`` lines 102-103 prevents any push
    when HEAD != target.
    """
    # Reconciler returns a divergence verdict: a non-fast-forward
    # push was rejected and the reconciler delegated to
    # ``reconcile_after_rejected_push``. The reconciler never
    # force-pushes; it carries forward the deferred publication
    # obligation so the next seam retries. Use ``REMOTE_FAILED``
    # (consistent with ``_remote_rejected_push``'s divergence outcome)
    # so the validator does not coerce ``last_outcome`` to UNKNOWN.
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
    # The boundary must NOT overwrite the divergence outcome with the
    # silent "no commits beyond target" skip.
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
    """AC-9: configured target and remote propagate through the boundary seam.

    With a non-default target (``release``) and a configured remote
    (``tacos``), the boundary must thread ``release`` to the
    reconciler (the publication reconciler owns the configured
    remote name — the boundary merely passes the configured target
    through). This pins the no-hard-coded-defaults contract.
    """
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
    """HEAD != target: feature-integration gate fires, reconciler is NOT called.

    The early return ``if branch_sha(root, target) != get_head_sha(root):
    return None`` must fire before ``_reconcile_remote_publication`` is
    called. This is the feature-integration gate — must stay intact.

    The previous iteration of this test created a real git repository
    and a feature branch to drive HEAD != target. With the boundary
    collaborators stubbed, the same precondition can be reproduced by
    having ``branch_sha`` and ``get_head_sha`` return different shas
    (the typed fake pattern). This pins the contract without
    requiring real git subprocess I/O.
    """
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
    # HEAD != target: branch is at "feature-sha", HEAD is at "main-sha".
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
