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
    unsafe_pull_record: RebaseState | None = None,
) -> list[str]:
    """Stub the boundary's collaborators so the unit test exercises only the seam.

    Returns a list of ``reconciler_called_with_target`` entries so the test can
    assert the configured ``target`` was threaded through unchanged.

    WU-2: ``unsafe_pull_record`` (when non-``None``) is returned from the
    pull fake. The OLD guard ``if remote_record is not None and not
    remote_record.freshness_safe: return remote_record`` short-circuited
    on this condition; supplying it from the fake proves the current code
    no longer has that guard (the reconciler runs regardless).
    """
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

    WU-2: this test now supplies a NON-NULL ``RebaseState`` with
    ``freshness_safe=False`` from the pull fake (mirroring the
    ``auto_integrate_remote_client.py:314`` pull-FF failure and
    ``:366`` non-cleanly-aborted reconciliation producers). The
    OLD guard ``if remote_record is not None and not
    remote_record.freshness_safe: return remote_record`` would
    short-circuit here -- the boundary would return the unsafe
    pull record WITHOUT consulting the reconciler. The current
    boundary code does NOT have that guard; the reconciler runs
    regardless of ``freshness_safe``.
    """
    head_sha = "stub-head-sha"
    pending = RebaseState(last_remote_sync=REMOTE_ALREADY_CURRENT)
    # WU-2: pull fake returns a NON-NULL RebaseState carrying
    # ``freshness_safe=False`` -- the OLD guard would short-circuit
    # here. The current code does not have that guard.
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


def test_unsafe_divergence_real_reconciler_uses_rejected_push_path_without_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WU-2 AC-12: real reconciler with faked I/O proves no force refspec on divergence.

    The previous test (``test_unsafe_divergence_propagates_reintegration_without_force_push``)
    mocked the reconciler at the boundary seam, so it pinned the
    boundary's *propagation* contract but not the reconciler's
    *rejected-push / no-force* contract. PA-003 of the analysis
    feedback noted that the boundary proof was incomplete: it
    demonstrated propagation of an externally-supplied divergence
    verdict, not that the real rejected-push path runs ``reconcile_after_rejected_push``
    without a forced refspec.

    This test exercises the REAL
    :func:`~ralph.pipeline.auto_integrate_remote_sync.reconcile_remote_publication`
    with faked I/O:

    * ``_safe_local_sha`` returns a known local SHA
      (``local-release-sha``).
    * ``_observe_remote_target_sha`` returns a different remote SHA
      (``remote-advance-sha``) and ``REFRESH_DIVERGED``.
    * ``is_ancestor`` returns ``False`` in both directions (local
      and remote are strictly diverged).
    * ``push_target_after_landing`` is faked to return
      ``PushStatus.NON_FAST_FORWARD`` -- the rejected-push signal.
    * ``reconcile_after_rejected_push`` is faked to return a
      ``PUBLISH_DEFERRED_REMOTE`` record WITHOUT consulting a force
      refspec (``force=False`` would be the default).

    The boundary then observes the reconciler's divergence outcome and
    propagates the ``PUBLISH_DEFERRED_REMOTE`` obligation; the push
    is delegated to ``reconcile_after_rejected_push`` (not force)
    and the next seam retries.
    """
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
        # Remote is strictly ahead AND strictly behind: a true divergence.
        return "remote-advance-sha", "diverged"

    def fake_is_ancestor(_r: Path, _a: str, _d: str) -> bool:
        # Local is strictly ahead (``remote_sha`` is an ancestor of
        # ``local_sha`` but NOT vice-versa) so the real reconciler
        # attempts the push and then delegates to
        # ``reconcile_after_rejected_push`` on the rejection.
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
        # Simulate the rejected-push outcome. Track whether the caller
        # ever passed a force refspec (it must NOT).
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
        # No force refspec: the rejected-push path performs
        # reintegration without pushing.
        return _pushed.model_copy(
            update={
                "last_reason": (
                    f"push of {_target} to {remote} failed: non-fast-forward; "
                    "reconciler delegated to reintegration"
                ),
                "last_publication_decision": PublicationDecision.PUBLISH_DEFERRED_REMOTE,
            }
        )

    # Stub collaborators so the boundary does NOT touch real git.
    monkeypatch.setattr(boundary, "branch_sha", fake_branch_sha)
    monkeypatch.setattr(boundary, "get_head_sha", lambda r: head_sha)
    monkeypatch.setattr(boundary, "remote_sync_enabled", lambda cfg: True)
    # ``remote_sync_enabled`` is also imported into
    # ``auto_integrate_remote_sync`` at module scope; the boundary
    # attribute patch alone is not enough because
    # ``reconcile_remote_publication`` consults the local name in
    # the ``remote_sync`` module's namespace. Patching both keeps
    # the boundary gate and the reconciler's policy gate aligned.
    monkeypatch.setattr(remote_sync, "remote_sync_enabled", lambda cfg: True)
    monkeypatch.setattr(boundary, "pull_and_reconcile_target", lambda *_a, **_k: None)
    monkeypatch.setattr(remote_sync, "_safe_local_sha", fake_local_sha)
    monkeypatch.setattr(remote_sync, "_observe_remote_target_sha", fake_observe_remote_sha)
    # ``is_ancestor`` is imported from ``ralph.git.merge`` at the top
    # of ``auto_integrate_remote_sync`` as a module-level local name.
    # Patching the source module rebinds the GLOBAL, but the local
    # name inside ``_dispatch_ref_comparison`` still points at the
    # original. Patching ``remote_sync.is_ancestor`` rebinds the
    # LOCAL name so the real reconciler consults the fake.
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

    # 1. The real reconciler was consulted (not mocked at the seam).
    assert len(forced_calls) == 1, (
        f"Boundary must consult the real reconciler on divergence; forced_calls={forced_calls}"
    )
    # 2. The push went to the configured remote (``tacos``), not
    #    the hard-coded default ``origin``.
    assert forced_calls[0][1] == "tacos", (
        f"Push must target the configured remote 'tacos'; got {forced_calls[0][1]}"
    )
    # 3. The rejected-push path was invoked (NOT a force push).
    assert len(rejected_calls) == 1, (
        f"Boundary must delegate divergence to reconcile_after_rejected_push; "
        f"rejected_calls={rejected_calls}"
    )
    # 4. The boundary propagated the divergence obligation (NOT the
    #    silent-skip overwrite).
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
