from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.auto_integrate import configured_resolver_allowed
from ralph.pipeline.auto_integrate_conflict_budget import (
    MAX_TRACKED_CONFLICT_ATTEMPTS,
    ConflictIdentity,
    apply_conflict_budget,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact

_RESOLUTION_PHASE = "rebase_conflict_resolution"

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_exhausted_feature_budget_does_not_block_a_distinct_remote_identity": (
        "the same launch-permission answer is asserted through the public configured_resolver_allowed seam; the private _configured_resolver_allowed import it replaces is rejected by the frozen white-box baseline"
    ),
    "test_no_ledger_does_not_apply_a_local_launch_veto": (
        "with no shared ledger the launch gate applies no local counter veto; chain "
        "dispatch sites that select among candidates separately require a non-empty set"
        "; the private _configured_resolver_allowed import is replaced by the public "
        "configured_resolver_allowed seam"
    ),
    "test_unchanged_paths_and_oids_are_the_same_conflict": (
        "launch permission is shared eligibility, not resolution_exhausted; same-conflict "
        "identity still matches while an Eligible ledger may launch"
    ),
    "test_changed_stage_oids_are_a_new_conflict": (
        "launch permission is shared eligibility; a new conflict identity is orthogonal to "
        "whether any_eligible reports an agent may run"
    ),
    "test_scope_separates_feature_and_remote_conflict_budgets": (
        "scope still separates conflict identity; launch permission follows any_eligible, "
        "not the durable local exhaustion flag"
    ),
    "test_resolver_eligibility_follows_the_ledger_not_the_stored_count": (
        "durable consecutive_conflicts / resolution_exhausted do not veto launch when the "
        "shared ledger reports Eligible"
    ),
    "test_a_cooling_resolver_is_not_launched": (
        "cooldown-strict any_eligible replaces the retired cooldown-blind all_exhausted branch"
    ),
    "test_budget_spend_advances_the_strategy_rung": (
        "apply_conflict_budget advances the strategy rung in the same step as the spend"
    ),
    "test_remote_refresh_books_conflict_identity_and_rejects_a_duplicate": (
        "remote refresh no longer suppresses the resolver from resolution_exhausted; "
        "launch permission is shared eligibility only"
    ),
}


def _ledger() -> AgentFailureLedger:
    return AgentFailureLedger(clock=FakeClock())


def _cooling_ledger(agent: str = "resolver") -> AgentFailureLedger:
    ledger = AgentFailureLedger(
        clock=FakeClock(),
        consecutive_limit=1,
        base_cooldown_seconds=10.0,
    )
    ledger.record(AttemptFact(phase=_RESOLUTION_PHASE, agent=agent, artifact_valid=False))
    return ledger


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
        consecutive_conflicts=MAX_TRACKED_CONFLICT_ATTEMPTS,
        last_conflict_feature_sha="feat",
        last_conflict_target_sha="main",
        last_conflict_paths=("a.py",),
        last_conflict_stage_oids=("oid-a",),
        resolution_exhausted=True,
    )
    assert identity.matches(state) is True
    assert (
        configured_resolver_allowed(
            state, "main", identity, ledger=_ledger(), candidates=("resolver",)
        )
        is True
    )


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
    assert (
        configured_resolver_allowed(
            state, "main", identity, ledger=_ledger(), candidates=("resolver",)
        )
        is True
    )


def test_budget_spend_advances_the_strategy_rung() -> None:
    identity = ConflictIdentity(feature_sha="feat", target_sha="main")
    updated = apply_conflict_budget(
        RebaseState(last_action="conflict", last_target="main"),
        prior=RebaseState(),
        target="main",
        resolver_suppressed=False,
        identity=identity,
        attempts=1,
    )
    assert updated.conflict_strategy_index == 1
    assert updated.consecutive_conflicts == 1


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
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_conflict_budget import (
        ConflictIdentity,
    )
    from ralph.pipeline.auto_integrate_remote_sync import pull_and_reconcile_target
    from ralph.pipeline.auto_integrate_sync import REFRESH_UNREACHABLE
    from ralph.pipeline.rebase_state import RebaseState

    identity = ConflictIdentity(
        feature_sha="feat",
        target_sha="main",
        conflicted_paths=("a.py",),
        stage_oids=("oid",),
    )
    observed: list[ConflictIdentity] = []
    applied: list[ConflictIdentity] = []
    suppressed: list[bool] = []

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
        suppressed.append(bool(kwargs.get("resolver_suppressed")))
        return record

    monkeypatch.setattr("ralph.pipeline.auto_integrate_remote_sync.apply_conflict_budget", _apply)

    config = UnifiedConfig.model_validate({"general": {}})
    prior = RebaseState(
        last_action="conflict",
        last_target="main",
        consecutive_conflicts=3,
        last_conflict_feature_sha="feat",
        last_conflict_target_sha="main",
        last_conflict_paths=("a.py",),
        last_conflict_stage_oids=("oid",),
        resolution_exhausted=True,
    )
    pull_and_reconcile_target(
        config,
        tmp_path,
        "main",
        rebase_stop_resolver=lambda **_kwargs: None,
        prior=prior,
    )
    assert observed == [identity]
    assert applied == [identity]
    assert suppressed == [False]


def test_remote_refresh_failure_still_books_conflict_budget(
    monkeypatch: object, tmp_path: Path
) -> None:
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


@pytest.mark.timeout_seconds(2.0)
def test_endpoint_merge_fallback_keeps_resolver_when_rebase_ownership_cannot_be_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.git.merge import MergeResult
    from ralph.git.rebase.rebase import RebaseConflicts
    from ralph.pipeline import auto_integrate_rebase_merge as merge_module

    endpoint_calls: list[object] = []
    aborted: list[Path] = []

    def _abort(repo_root: Path | None = None, **_kwargs: object) -> None:
        aborted.append(repo_root or tmp_path)

    @dataclass(frozen=True)
    class _Conflicts(RebaseConflicts):
        reason: str = "conflict"

    monkeypatch.setattr(merge_module, "_range_routing_reason", lambda _root, _target: None)
    monkeypatch.setattr(
        merge_module,
        "rebase_onto",
        lambda _target, repo_root: _Conflicts(files=["a.py"]),
    )

    def _ownership_unavailable(*_args: object) -> bool:
        return False

    def _unexpected_rebase_agent(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("rebase agent must not start without durable ownership")

    monkeypatch.setattr(merge_module, "set_resolving_rebase", _ownership_unavailable)
    monkeypatch.setattr(merge_module, "resolve_rebase_in_progress", _unexpected_rebase_agent)
    monkeypatch.setattr(merge_module, "rebase_in_progress", lambda _root: not aborted)
    monkeypatch.setattr(merge_module, "abort_rebase_discarding_progress", _abort)
    monkeypatch.setattr(merge_module, "_verify_terminal_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(merge_module, "_clear_record_if_no_inflight_op", lambda _root: None)

    def _endpoint(_root: Path, _target: str, resolver: object) -> MergeResult:
        endpoint_calls.append(resolver)
        return MergeResult(outcome="conflict")

    monkeypatch.setattr(merge_module, "endpoint_merge_with_resolution", _endpoint)

    def _merge_resolver(*_args: object) -> bool:
        return True

    result = merge_module.run_rebase_or_merge(
        tmp_path,
        "main",
        _merge_resolver,
        rebase_stop_resolver=lambda *_args: False,
    )
    assert endpoint_calls == [_merge_resolver]
    assert aborted == [tmp_path]
    assert result.merge_attempted


def test_exhausted_feature_budget_does_not_block_a_distinct_remote_identity() -> None:
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
        consecutive_conflicts=MAX_TRACKED_CONFLICT_ATTEMPTS,
        last_conflict_feature_sha="feat",
        last_conflict_target_sha="main",
        last_conflict_paths=("a.py",),
        last_conflict_stage_oids=("oid",),
        last_conflict_scope="feature",
        resolution_exhausted=True,
    )
    assert feature.matches(state) is True
    assert remote.matches(state) is False
    ledger = _ledger()
    assert (
        configured_resolver_allowed(state, "main", feature, ledger=ledger, candidates=("resolver",))
        is True
    )
    assert (
        configured_resolver_allowed(state, "main", remote, ledger=ledger, candidates=("resolver",))
        is True
    )


def test_resolver_eligibility_follows_the_ledger_not_the_stored_count() -> None:
    identity = ConflictIdentity(feature_sha="feat", target_sha="main")
    state = RebaseState(
        last_action="conflict",
        last_target="main",
        consecutive_conflicts=MAX_TRACKED_CONFLICT_ATTEMPTS,
        last_conflict_feature_sha="feat",
        last_conflict_target_sha="main",
        resolution_exhausted=True,
    )

    assert (
        configured_resolver_allowed(
            state, "main", identity, ledger=_ledger(), candidates=("resolver",)
        )
        is True
    )


def test_a_cooling_resolver_is_not_launched() -> None:
    identity = ConflictIdentity(feature_sha="feat", target_sha="main")
    state = RebaseState(
        last_action="conflict",
        last_target="main",
        consecutive_conflicts=0,
        last_conflict_feature_sha="feat",
        last_conflict_target_sha="main",
    )
    cooling = _cooling_ledger()
    assert not cooling.any_eligible(_RESOLUTION_PHASE, ("resolver",))
    assert not cooling.all_exhausted(_RESOLUTION_PHASE, ("resolver",))
    assert (
        configured_resolver_allowed(
            state,
            "main",
            identity,
            ledger=cooling,
            candidates=("resolver",),
        )
        is False
    )


def test_no_ledger_does_not_apply_a_local_launch_veto() -> None:
    identity = ConflictIdentity(feature_sha="feat", target_sha="main")
    state = RebaseState(
        last_action="conflict",
        last_target="main",
        consecutive_conflicts=MAX_TRACKED_CONFLICT_ATTEMPTS,
        last_conflict_feature_sha="feat",
        last_conflict_target_sha="main",
        resolution_exhausted=True,
    )

    assert (
        configured_resolver_allowed(state, "main", identity, ledger=None, candidates=("resolver",))
        is True
    )
    assert (
        configured_resolver_allowed(state, "main", identity, ledger=_ledger(), candidates=())
        is True
    )
