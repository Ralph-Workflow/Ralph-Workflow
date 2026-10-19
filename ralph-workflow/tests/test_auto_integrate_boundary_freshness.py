"""Tests for boundary_freshness_outcome: Bug A (freshness_safe gate) and Bug B (transient-failure overwrite).

AC-6: boundary consults the publication reconciler regardless of ``freshness_safe``.
AC-8: boundary preserves the reconciler's transient-failure verdict.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Actor, Repo

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate_boundary as boundary
from ralph.pipeline.auto_integrate_remote_sync import (
    REMOTE_ALREADY_CURRENT,
    REMOTE_PULL_FAILED,
    REMOTE_REMOTE_UNREACHABLE,
)
from ralph.pipeline.rebase_state import RebaseState


def _make_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
            }
        }
    )


def _make_real_repo(repo_root: Path) -> Repo:
    """Build a minimal real git repository for boundary tests."""
    repo = Repo.init(repo_root)
    repo.config_writer().set_value("user", "name", "Test Committer").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()
    actor = Actor("Test Committer", "test@example.com")
    readme = repo_root / "README.md"
    readme.write_text("test\n", encoding="utf-8")
    repo.index.add(["README.md"])
    repo.index.commit("initial commit", author=actor, committer=actor)
    repo.git.branch("-M", "main")
    return repo


@pytest.mark.timeout_seconds(5)
def test_freshness_safe_false_does_not_block_reconciler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-6: freshness_safe=False no longer gates the reconciler out.

    Before the fix, ``if remote_record is not None and not
    remote_record.freshness_safe: return remote_record`` short-circuited
    the boundary before ``_reconcile_remote_publication`` was ever called.
    After the fix the reconciler is always consulted.
    """
    repo = _make_real_repo(tmp_path)
    # Capture HEAD sha before any monkeypatch interferes with repo access.
    head_sha = repo.head.commit.hexsha

    reconcile_called = False

    def fake_pull_and_reconcile(config, root, target, *, rebase_stop_resolver, prior):
        # Return a record with freshness_safe=False — the old code would
        # have returned this immediately without calling the reconciler.
        return RebaseState(freshness_safe=False)

    def fake_reconcile_remote_publication(config, root, target, *, rebase_stop_resolver):
        nonlocal reconcile_called
        reconcile_called = True
        return RebaseState()

    monkeypatch.setattr(boundary, "pull_and_reconcile_target", fake_pull_and_reconcile)
    monkeypatch.setattr(
        boundary, "_reconcile_remote_publication", fake_reconcile_remote_publication
    )
    # Keep branch_sha == get_head_sha so the feature-integration gate
    # (line 102-103) does not fire and mask the AC-6 assertion.
    monkeypatch.setattr(boundary, "branch_sha", lambda r, t: head_sha)
    monkeypatch.setattr(boundary, "get_head_sha", lambda r: head_sha)
    # remote_sync_enabled must be False so _refresh_target is used (the
    # branch that calls record_when_stale). We do not care about its
    # return value for AC-6, only that _reconcile_remote_publication
    # was called.
    monkeypatch.setattr(boundary, "remote_sync_enabled", lambda cfg: False)

    result = boundary.boundary_freshness_outcome(
        _make_config(),
        tmp_path,
        "main",
        RebaseState(),
        rebase_stop_resolver=None,
    )

    assert reconcile_called, (
        "_reconcile_remote_publication was not called: "
        "the freshness_safe early-return (Bug A) is still active"
    )
    # The function should return pending (the reconciler's record).
    assert result is not None


@pytest.mark.timeout_seconds(5)
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
    _make_real_repo(tmp_path)

    pending_record = RebaseState(
        last_remote_sync=REMOTE_REMOTE_UNREACHABLE,
        last_reason="remote unreachable during pull",
    )

    def fake_pull_and_reconcile(config, root, target, *, rebase_stop_resolver, prior):
        return None

    def fake_reconcile_remote_publication(config, root, target, *, rebase_stop_resolver):
        return pending_record

    monkeypatch.setattr(boundary, "pull_and_reconcile_target", fake_pull_and_reconcile)
    monkeypatch.setattr(
        boundary, "_reconcile_remote_publication", fake_reconcile_remote_publication
    )
    monkeypatch.setattr(boundary, "branch_sha", lambda r, t: "fake-sha")
    monkeypatch.setattr(boundary, "get_head_sha", lambda r: "fake-sha")
    monkeypatch.setattr(boundary, "remote_sync_enabled", lambda cfg: False)

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


@pytest.mark.timeout_seconds(5)
def test_transient_failure_pull_failed_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-8 variant: REMOTE_PULL_FAILED is also preserved."""
    _make_real_repo(tmp_path)

    pending_record = RebaseState(
        last_remote_sync=REMOTE_PULL_FAILED,
        last_reason="pull failed: connection reset",
    )

    def fake_pull_and_reconcile(config, root, target, *, rebase_stop_resolver, prior):
        return None

    def fake_reconcile_remote_publication(config, root, target, *, rebase_stop_resolver):
        return pending_record

    monkeypatch.setattr(boundary, "pull_and_reconcile_target", fake_pull_and_reconcile)
    monkeypatch.setattr(
        boundary, "_reconcile_remote_publication", fake_reconcile_remote_publication
    )
    monkeypatch.setattr(boundary, "branch_sha", lambda r, t: "fake-sha")
    monkeypatch.setattr(boundary, "get_head_sha", lambda r: "fake-sha")
    monkeypatch.setattr(boundary, "remote_sync_enabled", lambda cfg: False)

    result = boundary.boundary_freshness_outcome(
        _make_config(),
        tmp_path,
        "main",
        RebaseState(),
        rebase_stop_resolver=None,
    )

    assert result is pending_record
    assert result.last_remote_sync == REMOTE_PULL_FAILED


@pytest.mark.timeout_seconds(5)
def test_already_current_returns_skip_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """already_current path: last_reason is overwritten to 'no commits beyond target' (correct)."""
    _make_real_repo(tmp_path)

    # The reconciler says already current.
    pending_record = RebaseState(
        last_remote_sync=REMOTE_ALREADY_CURRENT,
        last_reason="local and remote already converge",
    )
    # pull_and_reconcile_target returns a record so the
    # ``if remote_record is not None`` branch is taken.
    remote_record = RebaseState(freshness_safe=True)

    def fake_pull_and_reconcile(config, root, target, *, rebase_stop_resolver, prior):
        return remote_record

    def fake_reconcile_remote_publication(config, root, target, *, rebase_stop_resolver):
        return pending_record

    monkeypatch.setattr(boundary, "pull_and_reconcile_target", fake_pull_and_reconcile)
    monkeypatch.setattr(
        boundary, "_reconcile_remote_publication", fake_reconcile_remote_publication
    )
    monkeypatch.setattr(boundary, "branch_sha", lambda r, t: "fake-sha")
    monkeypatch.setattr(boundary, "get_head_sha", lambda r: "fake-sha")
    monkeypatch.setattr(boundary, "remote_sync_enabled", lambda cfg: False)

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


@pytest.mark.timeout_seconds(5)
def test_head_differs_from_target_gates_integration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HEAD != target: feature-integration gate fires, reconciler is NOT called.

    The early return ``if branch_sha(root, target) != get_head_sha(root):
    return None`` must fire before ``_reconcile_remote_publication`` is
    called. This is the feature-integration gate — must stay intact.
    """
    repo = _make_real_repo(tmp_path)

    reconcile_called = False

    def fake_pull_and_reconcile(config, root, target, *, rebase_stop_resolver, prior):
        # freshness_safe=False on this record would have triggered Bug A
        # in the old code, but the feature-integration gate fires first.
        return RebaseState(freshness_safe=False)

    def fake_reconcile_remote_publication(config, root, target, *, rebase_stop_resolver):
        nonlocal reconcile_called
        reconcile_called = True
        return RebaseState()

    # Create a feature branch and check it out so HEAD != main.
    repo.git.checkout("-b", "feature/work")
    actor = Actor("Test Committer", "test@example.com")
    feature_file = tmp_path / "feature.txt"
    feature_file.write_text("feature\n", encoding="utf-8")
    repo.index.add(["feature.txt"])
    repo.index.commit("feature commit", author=actor, committer=actor)

    monkeypatch.setattr(boundary, "pull_and_reconcile_target", fake_pull_and_reconcile)
    monkeypatch.setattr(
        boundary, "_reconcile_remote_publication", fake_reconcile_remote_publication
    )
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
        "the feature-integration gate (line 102-103) did not fire"
    )
    assert not reconcile_called, (
        "_reconcile_remote_publication was called despite HEAD != target: "
        "the feature-integration gate was bypassed"
    )
