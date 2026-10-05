"""Shared conflict identity and budget across rebase, endpoint merge, and remote refresh."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline.auto_integrate_conflict_budget import (
    MAX_CONSECUTIVE_RESOLVER_ATTEMPTS,
    ConflictIdentity,
    apply_conflict_budget,
    resolver_allowed,
)
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    import pytest


def test_unchanged_paths_and_oids_are_the_same_conflict() -> None:
    identity = ConflictIdentity(
        feature_sha="feat",
        target_sha="main",
        conflicted_paths=("a.py",),
        stage_oids=("oid-a",),
    )
    state = RebaseState(
        last_action="conflict",
        last_target="main",
        consecutive_conflicts=MAX_CONSECUTIVE_RESOLVER_ATTEMPTS,
        last_conflict_feature_sha="feat",
        last_conflict_target_sha="main",
        last_conflict_paths=("a.py",),
        last_conflict_stage_oids=("oid-a",),
    )
    assert identity.matches(state) is True
    assert resolver_allowed(state, "main", identity) is False


def test_changed_stage_oids_are_a_new_conflict() -> None:
    identity = ConflictIdentity(
        feature_sha="feat",
        target_sha="main",
        conflicted_paths=("a.py",),
        stage_oids=("oid-b",),
    )
    state = RebaseState(
        last_action="conflict",
        last_target="main",
        consecutive_conflicts=2,
        last_conflict_feature_sha="feat",
        last_conflict_target_sha="main",
        last_conflict_paths=("a.py",),
        last_conflict_stage_oids=("oid-a",),
    )
    assert identity.matches(state) is False
    assert resolver_allowed(state, "main", identity) is True


def test_apply_conflict_budget_persists_path_identity() -> None:
    prior = RebaseState()
    identity = ConflictIdentity(
        feature_sha="feat",
        target_sha="tgt",
        conflicted_paths=("src/a.py",),
        stage_oids=("1:abc",),
    )
    record = RebaseState(last_action="conflict", last_target="tgt")
    updated = apply_conflict_budget(
        record, prior=prior, target="tgt", resolver_suppressed=False, identity=identity
    )
    assert updated.last_conflict_paths == ("src/a.py",)
    assert updated.last_conflict_stage_oids == ("1:abc",)


def test_observe_conflict_identity_includes_paths_and_oids(
    monkeypatch: object, tmp_path: Path
) -> None:
    from ralph.pipeline.auto_integrate_budget_seam import observe_conflict_identity

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_budget_seam.get_head_sha", lambda _root: "feat"
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_budget_seam.branch_sha", lambda _root, _target: "main"
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_budget_seam.unmerged_paths", lambda _root: ["a.py"]
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_budget_seam.conflict_stage_entries",
        lambda _root, _paths: {"a.py": {2: ("100644", "blob-ours"), 3: ("100644", "blob-theirs")}},
    )
    identity = observe_conflict_identity(tmp_path, "main")
    assert identity.conflicted_paths == ("a.py",)
    assert "a.py:2:blob-ours" in identity.stage_oids
    assert "a.py:3:blob-theirs" in identity.stage_oids


def test_remote_refresh_books_conflict_identity_and_rejects_a_duplicate(
    monkeypatch: object, tmp_path: Path
) -> None:
    """Remote refresh observes ConflictIdentity and cannot start a duplicate attempt."""
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_conflict_budget import (
        ConflictIdentity,
        start_conflict_attempt,
    )
    from ralph.pipeline.auto_integrate_remote_sync import pull_and_reconcile_target
    from ralph.pipeline.auto_integrate_sync import REFRESH_UNREACHABLE

    identity = ConflictIdentity(
        feature_sha="feat",
        target_sha="main",
        conflicted_paths=("a.py",),
        stage_oids=("oid",),
    )
    observed: list[ConflictIdentity] = []
    applied: list[ConflictIdentity] = []
    started: list[bool] = []

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync.observe_conflict_identity",
        lambda *_args, **_kwargs: observed.append(identity) or identity,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync.remote_sync_enabled",
        lambda _config: True,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync.remote_target_name",
        lambda _config: "origin",
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync._throttle_allows_pull",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync.refresh_target_from_remote",
        lambda *_args, **_kwargs: REFRESH_UNREACHABLE,
    )

    def _apply(record: object, **kwargs: object) -> object:
        ident = kwargs.get("identity")
        if isinstance(ident, ConflictIdentity):
            applied.append(ident)
        return record

    monkeypatch.setattr("ralph.pipeline.auto_integrate_remote_sync.apply_conflict_budget", _apply)

    original_start = start_conflict_attempt

    def _start(ident: ConflictIdentity) -> bool:
        allowed = original_start(ident)
        started.append(allowed)
        return allowed

    monkeypatch.setattr("ralph.pipeline.auto_integrate_remote_sync.start_conflict_attempt", _start)

    config = UnifiedConfig.model_validate({"general": {}})
    assert start_conflict_attempt(identity) is True
    pull_and_reconcile_target(
        config,
        tmp_path,
        "main",
        rebase_stop_resolver=lambda **_kwargs: None,
    )
    assert observed == [identity]
    assert started == [False]
    assert applied == [identity]


def test_remote_refresh_failure_still_books_conflict_budget(
    monkeypatch: object, tmp_path: Path
) -> None:
    """A None pull outcome must still run apply_conflict_budget before returning."""
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity
    from ralph.pipeline.auto_integrate_remote_sync import pull_and_reconcile_target

    identity = ConflictIdentity(scope="remote")
    applied: list[ConflictIdentity] = []
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync.observe_conflict_identity",
        lambda *_args, **_kwargs: identity,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync.remote_sync_enabled",
        lambda _config: True,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync.remote_target_name",
        lambda _config: "origin",
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync._throttle_allows_pull",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync.refresh_target_from_remote",
        lambda *_args, **_kwargs: "ok",
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync._dispatch_pull_outcome",
        lambda *_args, **_kwargs: None,
    )

    def _apply(record: object, **kwargs: object) -> object:
        ident = kwargs.get("identity")
        if isinstance(ident, ConflictIdentity):
            applied.append(ident)
        return record

    monkeypatch.setattr("ralph.pipeline.auto_integrate_remote_sync.apply_conflict_budget", _apply)
    config = UnifiedConfig.model_validate({"general": {}})
    result = pull_and_reconcile_target(config, tmp_path, "main")
    assert applied == [identity]
    assert result is not None


def test_endpoint_merge_fallback_keeps_the_resolver_after_a_failed_rebase_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The merge fallback is the last way to land the target, so it keeps its resolver.

    It used to be handed ``None`` after a failed commit-by-commit rebase
    resolution, so any conflicted merge aborted and the branch stayed
    behind its target -- planning then ran on stale code. One whole-branch
    merge is a different integration shape from the replay, and the
    anti-thrash budget still bounds repeated attempts across seams.
    """
    from ralph.git.merge import MergeResult
    from ralph.git.rebase.rebase import RebaseConflicts
    from ralph.pipeline import auto_integrate_rebase_merge as merge_module
    from ralph.pipeline.auto_integrate_conflict_budget import (
        ConflictIdentity,
        finish_conflict_attempt,
        start_conflict_attempt,
    )

    identity = ConflictIdentity(
        feature_sha="feat",
        target_sha="main",
        conflicted_paths=("a.py",),
        stage_oids=("oid",),
    )
    finish_conflict_attempt(identity)
    assert start_conflict_attempt(identity) is True
    endpoint_calls: list[object] = []
    aborted: list[Path] = []

    def _abort(repo_root: Path | None = None, **_kwargs: object) -> None:
        aborted.append(repo_root or tmp_path)

    monkeypatch.setattr(merge_module, "_range_routing_reason", lambda _root, _target: None)
    monkeypatch.setattr(
        merge_module,
        "rebase_onto",
        lambda _target, repo_root: RebaseConflicts(files=["a.py"]),
    )
    monkeypatch.setattr(merge_module, "set_resolving_rebase", lambda *_args: True)
    monkeypatch.setattr(merge_module, "resolve_rebase_in_progress", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(merge_module, "rebase_in_progress", lambda _root: not aborted)
    monkeypatch.setattr(merge_module, "abort_rebase_discarding_progress", _abort)
    monkeypatch.setattr(merge_module, "_rebase_has_landed_stops", lambda _root: False)
    monkeypatch.setattr(merge_module, "_verify_terminal_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(merge_module, "_clear_record_if_no_inflight_op", lambda _root: None)

    def _endpoint(_root: Path, _target: str, resolver: object) -> MergeResult:
        endpoint_calls.append(resolver)
        return MergeResult(outcome="conflict")

    monkeypatch.setattr(merge_module, "endpoint_merge_with_resolution", _endpoint)

    def _merge_resolver(*_args: object) -> bool:
        return True

    try:
        merge_module.run_rebase_or_merge(
            tmp_path,
            "main",
            _merge_resolver,
            rebase_stop_resolver=lambda *_args: False,
        )
        assert endpoint_calls == [_merge_resolver]
    finally:
        finish_conflict_attempt(identity)


def test_exhausted_feature_budget_does_not_block_a_distinct_remote_identity() -> None:
    """Remote reconciliation must keep its own budget even when feature spend is done."""
    feature = ConflictIdentity(
        feature_sha="feat",
        target_sha="main",
        conflicted_paths=("a.py",),
        stage_oids=("oid",),
    )
    remote = ConflictIdentity(
        feature_sha="feat",
        target_sha="main",
        conflicted_paths=("a.py",),
        stage_oids=("oid",),
        scope="remote",
    )
    state = RebaseState(
        last_action="conflict",
        last_target="main",
        consecutive_conflicts=MAX_CONSECUTIVE_RESOLVER_ATTEMPTS,
        last_conflict_feature_sha="feat",
        last_conflict_target_sha="main",
        last_conflict_paths=("a.py",),
        last_conflict_stage_oids=("oid",),
        last_conflict_scope="feature",
    )
    assert resolver_allowed(state, "main", feature) is False
    assert resolver_allowed(state, "main", remote) is True
