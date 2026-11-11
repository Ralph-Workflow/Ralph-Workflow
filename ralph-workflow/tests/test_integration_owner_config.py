from __future__ import annotations

from pathlib import Path
from typing import Never

import pytest

from ralph.pipeline import (
    _integration_action,
    _integration_owner_seam,
    integration_director,
    integration_owner,
)
from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline._integration_owner_seam import decide_seam_action
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.integration_attempt_ledger import AttemptLedger, AttemptLedgerState
from ralph.pipeline.integration_decision import AttemptState, CleanupLadderRung, EscalationRung
from ralph.pipeline.integration_observation import (
    HeadState,
    InProgressKind,
    InProgressOwnership,
    IntegrationRecordState,
    Observation,
    TargetVsFeature,
    TargetVsRemote,
    WorktreeState,
)
from ralph.pipeline.integration_owner import (
    RecordFactOutcome,
    decide_integration,
    ladder_rung_for_record_fact_outcome,
)


def _observation(
    *,
    target_vs_feature: TargetVsFeature = TargetVsFeature.EQUAL,
    target_vs_remote: TargetVsRemote = TargetVsRemote.EQUAL,
    worktree: WorktreeState = WorktreeState.CLEAN,
    head: HeadState = HeadState.ATTACHED,
    in_progress_kind: InProgressKind = InProgressKind.NONE,
    in_progress_ownership: InProgressOwnership = InProgressOwnership.OWNED,
    record: IntegrationRecordState = IntegrationRecordState.NONE,
    agent_active: bool = False,
) -> Observation:
    return Observation(
        target_vs_feature=target_vs_feature,
        target_vs_remote=target_vs_remote,
        worktree=worktree,
        head=head,
        in_progress_kind=in_progress_kind,
        in_progress_ownership=in_progress_ownership,
        record=record,
        agent_active=agent_active,
    )


class _RecordingDirector:
    def __init__(
        self,
        root: Path,
        *,
        target: str,
        remote: str,
        agent_active: bool = False,
        observation: Observation,
    ) -> None:
        self.root = root
        self.target = target
        self.remote = remote
        self.agent_active = agent_active
        self.observation = observation

    def observe(self) -> Observation:
        return self.observation


def _install_director(
    monkeypatch: pytest.MonkeyPatch,
    observation: Observation,
) -> list[_RecordingDirector]:
    created: list[_RecordingDirector] = []

    def _factory(
        root: Path,
        *,
        target: str,
        remote: str,
        agent_active: bool = False,
    ) -> _RecordingDirector:
        director = _RecordingDirector(
            root,
            target=target,
            remote=remote,
            agent_active=agent_active,
            observation=observation,
        )
        created.append(director)
        return director

    monkeypatch.setattr(integration_director, "IntegrationDirector", _factory)
    return created


def _ledger_path(tmp_path: Path) -> Path:
    return tmp_path / ".agent" / "integration_attempt_ledger.json"


def _ledger_state(tmp_path: Path) -> AttemptLedgerState:
    return AttemptLedger(tmp_path, ledger_path=_ledger_path(tmp_path)).read()


def _signature(observation: Observation) -> str:
    return AttemptState().signature(observation)


def _record(
    *,
    target: str = "release",
    diagnostic_intervention_required: bool = False,
) -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrating",
        target=target,
        pre_feature_sha="0" * 40,
        pre_target_sha="0" * 40,
        diagnostic_intervention_required=diagnostic_intervention_required,
    )


def test_configured_target_and_remote_reach_the_director(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    created = _install_director(monkeypatch, _observation())

    decide_integration(
        tmp_path,
        target="release",
        remote="tacos",
        ledger_path=_ledger_path(tmp_path),
    )

    assert len(created) == 1
    assert created[0].target == "release"
    assert created[0].remote == "tacos"
    assert created[0].target != integration_director.DEFAULT_TARGET_BRANCH
    assert created[0].remote != integration_director.DEFAULT_REMOTE


def test_the_seam_forwards_the_configured_target_and_remote(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    created = _install_director(monkeypatch, _observation())

    decide_seam_action(
        tmp_path,
        target="release",
        remote="tacos",
        ledger_path=_ledger_path(tmp_path),
    )

    assert len(created) == 1
    assert created[0].target == "release"
    assert created[0].remote == "tacos"


def test_without_configured_names_the_director_defaults_apply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    created = _install_director(monkeypatch, _observation())

    decide_integration(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert len(created) == 1
    assert created[0].target == integration_director.DEFAULT_TARGET_BRANCH
    assert created[0].remote == integration_director.DEFAULT_REMOTE


def test_a_stale_record_claim_does_not_block_a_healthy_git_observation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_director(monkeypatch, _observation())
    stale = _record(diagnostic_intervention_required=True)

    action = decide_seam_action(
        tmp_path,
        record=stale,
        ledger_path=_ledger_path(tmp_path),
    )

    assert action is IntegrationAction.LAND_TARGET


def test_a_live_intervention_claim_still_fails_closed_to_the_ladder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_director(
        monkeypatch,
        _observation(target_vs_feature=TargetVsFeature.BEHIND),
    )
    stuck = _record(diagnostic_intervention_required=True)

    action = decide_seam_action(
        tmp_path,
        record=stuck,
        ledger_path=_ledger_path(tmp_path),
    )

    assert action is RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT
    assert ladder_rung_for_record_fact_outcome(action) is CleanupLadderRung.NORMAL_CLEANUP


def test_a_record_owned_by_another_worktree_does_not_block_this_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_director(
        monkeypatch,
        _observation(
            target_vs_feature=TargetVsFeature.BEHIND,
            record=IntegrationRecordState.FOREIGN,
        ),
    )
    foreign = _record(diagnostic_intervention_required=True)

    action = decide_seam_action(
        tmp_path,
        record=foreign,
        ledger_path=_ledger_path(tmp_path),
    )

    assert action is IntegrationAction.RESOLVE_ACTIVE_OPERATION


def test_an_unknown_environment_never_releases_a_durable_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_director(
        monkeypatch,
        _observation(
            target_vs_feature=TargetVsFeature.UNKNOWN,
            worktree=WorktreeState.UNKNOWN,
        ),
    )
    stuck = _record(diagnostic_intervention_required=True)

    action = decide_seam_action(
        tmp_path,
        record=stuck,
        ledger_path=_ledger_path(tmp_path),
    )

    assert action is RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT
    assert ladder_rung_for_record_fact_outcome(action) is CleanupLadderRung.NORMAL_CLEANUP


def test_live_ownership_decides_who_resumes_the_operation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_director(
        monkeypatch,
        _observation(
            in_progress_kind=InProgressKind.REBASE,
            in_progress_ownership=InProgressOwnership.OWNED,
        ),
    )

    action = decide_seam_action(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert action is IntegrationAction.CONTINUE_GIT_OPERATION


def test_attempt_history_still_advances_the_escalation_ladder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observation = _observation(worktree=WorktreeState.UNMERGED)
    _install_director(monkeypatch, observation)
    ledger_path = _ledger_path(tmp_path)

    decide_seam_action(tmp_path, ledger_path=ledger_path)
    decide_seam_action(tmp_path, ledger_path=ledger_path)

    state = _ledger_state(tmp_path)
    assert state.repeat_counts == {(_signature(observation), "dispatch_resolver"): 2}
    assert state.rung is EscalationRung.DISPATCH_RESOLVER


def test_the_seam_persists_exactly_one_charge_per_mutation_class_decision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observation = _observation(worktree=WorktreeState.UNMERGED)
    _install_director(monkeypatch, observation)

    action = decide_seam_action(
        tmp_path,
        target="release",
        remote="tacos",
        ledger_path=_ledger_path(tmp_path),
    )

    assert action is IntegrationAction.RESOLVE_ACTIVE_OPERATION
    state = _ledger_state(tmp_path)
    assert state.repeat_counts == {(_signature(observation), "dispatch_resolver"): 1}


def test_the_public_owner_path_does_not_invoke_the_retired_record_decider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_director(monkeypatch, _observation())

    def _retired_decider(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("the public owner path invoked the retired record decider")

    monkeypatch.setattr(
        _integration_action,
        "next_integration_action",
        _retired_decider,
    )
    monkeypatch.setattr(
        integration_owner,
        "next_integration_action",
        _retired_decider,
        raising=False,
    )
    monkeypatch.setattr(
        _integration_owner_seam,
        "next_integration_action",
        _retired_decider,
        raising=False,
    )

    action = decide_seam_action(
        tmp_path,
        ledger_path=_ledger_path(tmp_path),
    )

    assert action is IntegrationAction.LAND_TARGET
