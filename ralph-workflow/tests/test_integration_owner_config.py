"""Configured-target and Git-outranks-the-record tests for the single owner.

The owner (:mod:`ralph.pipeline.integration_owner`) is the ONE place an
integration decision is produced, so it is also the ONE place the
CONFIGURED target branch and remote name must enter the observation
layer. These tests pin two contracts:

* **The configured target/remote reach the director.** A configured
  non-default pair (``release`` / ``tacos``) must be handed to the
  director instead of the hardcoded ``main`` / ``origin`` defaults,
  otherwise the observation is taken against a branch the operator
  never configured and a configured target could never converge.
* **Git outranks the record.** The durable record holds intent,
  ownership, attempt history and legacy-format identity -- never a fact
  Git can answer. A stale durable claim (a record that still says "a
  repair is stuck" after the work landed, or a record another worktree
  owns) must NOT turn a current, healthy Git observation into
  ``REQUIRE_INTERVENTION``, which is a terminal block the boundary can
  never leave.

The legitimate record facts keep working: the live ownership projection
still decides who resumes an operation, the durable attempt history
still advances the escalation ladder, and a live intervention claim
still fails closed.

No real Git, no subprocess, no sleeps: the director is replaced by a
recording stand-in and the durable ledger is written by the production
code under ``tmp_path``. This is the ``audit_test_policy`` contract.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from ralph.pipeline import integration_director, integration_owner
from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline._integration_owner_seam import decide_seam_action
from ralph.pipeline._integration_record_facts import apply_record_facts
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.integration_attempt_ledger import AttemptLedger, AttemptLedgerState
from ralph.pipeline.integration_decision import AttemptState, EscalationRung
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
from ralph.pipeline.integration_owner import decide_integration

# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


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
    """Build a synthetic seven-dimension observation."""
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
    """Director stand-in that records the names it was constructed with."""

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
        """Return the canned observation the test installed."""
        return self.observation


def _install_director(
    monkeypatch: pytest.MonkeyPatch,
    observation: Observation,
) -> list[_RecordingDirector]:
    """Replace the director with a recording stand-in; return the instances."""
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
    """Return the durable attempt-ledger path used by one test."""
    return tmp_path / ".agent" / "integration_attempt_ledger.json"


def _ledger_state(tmp_path: Path) -> AttemptLedgerState:
    """Read the durable ledger state back from disk."""
    return AttemptLedger(tmp_path, ledger_path=_ledger_path(tmp_path)).read()


def _signature(observation: Observation) -> str:
    """Return the durable state signature the ledger charges against."""
    return AttemptState().signature(observation)


def _record(
    *,
    target: str = "release",
    diagnostic_intervention_required: bool = False,
) -> IntegrationRecord:
    """Build a durable integration record for the record-fact tests."""
    return IntegrationRecord(
        phase="integrating",
        target=target,
        pre_feature_sha="0" * 40,
        pre_target_sha="0" * 40,
        diagnostic_intervention_required=diagnostic_intervention_required,
    )


# ---------------------------------------------------------------------------
# The configured target and remote reach the director.
# ---------------------------------------------------------------------------


def test_configured_target_and_remote_reach_the_director(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The owner observes the CONFIGURED names, not the hardcoded pair."""
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
    """The seam carries the configured names into the owner's observation."""
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
    """A caller with no configured name gets the director's own defaults."""
    created = _install_director(monkeypatch, _observation())

    decide_integration(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert len(created) == 1
    assert created[0].target == integration_director.DEFAULT_TARGET_BRANCH
    assert created[0].remote == integration_director.DEFAULT_REMOTE


# ---------------------------------------------------------------------------
# Git outranks the record.
# ---------------------------------------------------------------------------


def test_a_stale_record_claim_does_not_block_a_healthy_git_observation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A durable "intervention required" flag cannot outrank a landed tree.

    The record still claims the repair is stuck, but the live Git state
    proves the work landed: the feature is contained in the target, the
    worktree is clean and no Git operation is in flight. The durable
    claim is stale intent, so the owner's answer stands.
    """
    _install_director(monkeypatch, _observation())
    stale = _record(diagnostic_intervention_required=True)

    action = decide_seam_action(
        tmp_path,
        record=stale,
        ledger_path=_ledger_path(tmp_path),
    )

    assert action is IntegrationAction.LAND_TARGET


def test_a_live_intervention_claim_still_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A live durable claim still blocks while its obligation is live.

    The target has NOT received the feature, so the durable repair-cap
    fact still describes a real situation: the boundary fails closed to
    ``REQUIRE_INTERVENTION``.
    """
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

    assert action is IntegrationAction.REQUIRE_INTERVENTION


def test_a_record_owned_by_another_worktree_does_not_block_this_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A foreign record's claim is not about this workspace.

    The observation projects the durable record as ``FOREIGN`` (another
    worktree owns it), so its repair-cap fact describes another
    workspace's operation and must not escalate this workspace into a
    terminal block. The live owner still decides the rung.
    """
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
    """Only a PROVEN healthy Git state may release a fail-closed verdict.

    An unreadable probe is not proof of health: the durable claim keeps
    the fail-closed verdict.
    """
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

    assert action is IntegrationAction.REQUIRE_INTERVENTION


# ---------------------------------------------------------------------------
# The legitimate record facts keep working.
# ---------------------------------------------------------------------------


def test_live_ownership_decides_who_resumes_the_operation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The owner honours the live ownership projection of the operation."""
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
    """The durable attempt history still drives the escalation ladder."""
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
    """One mutation-class decision persists exactly one escalation charge."""
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


# ---------------------------------------------------------------------------
# The owner path does not re-derive the rung from a durable record.
# ---------------------------------------------------------------------------


def test_the_owner_path_does_not_call_the_retired_record_decider() -> None:
    """The owner path must not re-derive the ladder from a durable record.

    ``next_integration_action`` is the retired record-based decider: the
    owner (and the seam that asks it) must never call it, or the
    integration rung would be decided in two places.
    """
    assert "next_integration_action(" not in inspect.getsource(integration_owner)
    for function in (decide_seam_action, apply_record_facts):
        module = inspect.getmodule(function)
        assert module is not None
        assert "next_integration_action(" not in inspect.getsource(module)
