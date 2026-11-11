from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline import integration_director, integration_owner_seam
from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline._integration_owner_seam import (
    PlanningSyncVerdict,
    decide_planning_sync,
    execute_planning_ladder_rung,
)
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.integration_decision import AttemptState, CleanupLadderRung
from ralph.pipeline.integration_ladder import LAST_RESORT_DISABLED_NO_SIGNATURE, LadderStepResult
from ralph.pipeline.integration_observation import (
    DirtShape,
    HeadState,
    InProgressKind,
    InProgressOwnership,
    IntegrationRecordState,
    Observation,
    Reading,
    TargetVsFeature,
    TargetVsRemote,
    WorktreeDispatch,
    WorktreeState,
    WorktreeVerdict,
)
from tests._integration_ladder_fakes import _FakePlumbing, _FakeQuarantineFs

pytestmark = pytest.mark.timeout_seconds(10)

_TARGET_CONTAINED = (TargetVsFeature.EQUAL, TargetVsFeature.AHEAD)


def _observation(
    *,
    target_vs_feature: TargetVsFeature = TargetVsFeature.EQUAL,
    worktree: WorktreeState = WorktreeState.CLEAN,
) -> Observation:
    return Observation(
        target_vs_feature=target_vs_feature,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=worktree,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.NONE,
        agent_active=False,
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


def _install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    observation: Observation,
    porcelain: str,
) -> None:
    del tmp_path

    def _factory(
        root: Path,
        *,
        target: str,
        remote: str,
        agent_active: bool = False,
    ) -> _RecordingDirector:
        return _RecordingDirector(
            root,
            target=target,
            remote=remote,
            agent_active=agent_active,
            observation=observation,
        )

    monkeypatch.setattr(integration_director, "IntegrationDirector", _factory)
    monkeypatch.setattr(
        integration_owner_seam,
        "read_worktree_porcelain",
        lambda _root: Reading.known(porcelain),
    )


def _ledger_path(tmp_path: Path) -> Path:
    return tmp_path / ".agent" / "integration_attempt_ledger.json"


@pytest.mark.parametrize("target_vs_feature", _TARGET_CONTAINED)
def test_in_sync_clean_tree_allows_planning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_vs_feature: TargetVsFeature,
) -> None:
    _install(monkeypatch, tmp_path, _observation(target_vs_feature=target_vs_feature), "")

    verdict = decide_planning_sync(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert verdict.in_sync is True
    assert verdict.may_plan is True
    assert verdict.worktree.state is WorktreeState.CLEAN
    assert verdict.dirt_shape is DirtShape.NONE
    assert verdict.reason


@pytest.mark.parametrize("target_vs_feature", _TARGET_CONTAINED)
def test_tracked_dirt_holds_planning_with_a_typed_shape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_vs_feature: TargetVsFeature,
) -> None:
    _install(
        monkeypatch,
        tmp_path,
        _observation(target_vs_feature=target_vs_feature, worktree=WorktreeState.TRACKED_CHANGES),
        "M  src/app.py\n",
    )

    verdict = decide_planning_sync(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert verdict.in_sync is True
    assert verdict.may_plan is False
    assert verdict.dirt_shape is DirtShape.STAGED_ONLY
    assert verdict.worktree.dispatch_blocked is True


def test_out_of_sync_branch_holds_planning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install(
        monkeypatch,
        tmp_path,
        _observation(target_vs_feature=TargetVsFeature.BEHIND),
        "",
    )

    verdict = decide_planning_sync(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert verdict.in_sync is False
    assert verdict.may_plan is False
    assert "target" in verdict.reason


def test_untracked_only_does_not_hold_planning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install(monkeypatch, tmp_path, _observation(), "?? notes.txt\n")

    verdict = decide_planning_sync(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert verdict.may_plan is True
    assert verdict.dirt_shape is DirtShape.UNTRACKED_ONLY


def test_unknown_probe_holds_planning_without_a_verdict_guess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observation = _observation(worktree=WorktreeState.UNKNOWN)
    _install(monkeypatch, tmp_path, observation, " M src/app.py\n")
    monkeypatch.setattr(
        integration_owner_seam,
        "read_worktree_porcelain",
        lambda _root: Reading.unknown("git status timed out"),
    )

    verdict = decide_planning_sync(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert verdict.may_plan is False
    assert verdict.worktree.dispatch is not verdict.worktree.dispatch_blocked
    assert verdict.worktree.needs_reobservation is True


def test_every_call_charges_the_durable_ledger_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install(
        monkeypatch,
        tmp_path,
        _observation(target_vs_feature=TargetVsFeature.BEHIND),
        " M src/app.py\n",
    )

    first = decide_planning_sync(tmp_path, ledger_path=_ledger_path(tmp_path))
    second = decide_planning_sync(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert first.may_plan is False
    assert second.may_plan is False
    ledger = AttemptLedger(tmp_path, ledger_path=_ledger_path(tmp_path)).read()
    signature = AttemptState().signature(first.observation)
    total = sum(
        count
        for (key_signature, _action), count in ledger.repeat_counts.items()
        if key_signature == signature
    )
    assert total == 2, f"a repeated hold must charge twice, charged {total}"


def test_the_verdict_carries_the_owner_action(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install(
        monkeypatch,
        tmp_path,
        _observation(target_vs_feature=TargetVsFeature.BEHIND),
        " M src/app.py\n",
    )

    verdict = decide_planning_sync(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert isinstance(verdict.action, IntegrationAction)
    assert verdict.action is IntegrationAction.REINTEGRATE_AGAINST_TARGET
    assert verdict.observation.target_vs_feature is TargetVsFeature.BEHIND


def test_configured_target_reaches_the_observation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    created: list[_RecordingDirector] = []

    def _factory(
        root: Path,
        *,
        target: str,
        remote: str,
        agent_active: bool = False,
    ) -> _RecordingDirector:
        del agent_active
        director = _RecordingDirector(
            root,
            target=target,
            remote=remote,
            observation=_observation(),
        )
        created.append(director)
        return director

    monkeypatch.setattr(integration_director, "IntegrationDirector", _factory)
    monkeypatch.setattr(
        integration_owner_seam,
        "read_worktree_porcelain",
        lambda _root: Reading.known(""),
    )

    decide_planning_sync(
        tmp_path,
        target="release",
        remote="tacos",
        ledger_path=_ledger_path(tmp_path),
    )

    assert created[0].target == "release"
    assert created[0].remote == "tacos"


def _verdict_with_rung(rung: CleanupLadderRung | None) -> PlanningSyncVerdict:
    return PlanningSyncVerdict(
        action=IntegrationAction.CONTINUE_GIT_OPERATION,
        observation=_observation(),
        worktree=WorktreeVerdict(
            state=WorktreeState.TRACKED_CHANGES,
            is_clean=False,
            dispatch=WorktreeDispatch.BLOCKED,
            reason="tracked changes",
            dirt_shape=DirtShape.UNSTAGED_MODIFICATION,
        ),
        dirt_shape=DirtShape.UNSTAGED_MODIFICATION,
        in_sync=True,
        may_plan=False,
        reason="held: residue cleanup ladder",
        ladder_rung=rung,
    )


def test_the_verdict_carries_the_selected_cleanup_ladder_rung() -> None:
    assert _verdict_with_rung(CleanupLadderRung.QUARANTINE).ladder_rung is (
        CleanupLadderRung.QUARANTINE
    )
    assert _verdict_with_rung(None).ladder_rung is None


def test_execute_planning_ladder_rung_dispatches_the_selected_rung_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[CleanupLadderRung, dict[str, object]]] = []
    result = LadderStepResult(CleanupLadderRung.QUARANTINE, "succeeded", "succeeded:test")

    def _fake_run_ladder_rung(rung: CleanupLadderRung, **kwargs: object) -> LadderStepResult:
        calls.append((rung, kwargs))
        return result

    monkeypatch.setattr(integration_owner_seam, "run_ladder_rung", _fake_run_ladder_rung)
    ledger = AttemptLedger(tmp_path, ledger_path=_ledger_path(tmp_path))
    verdict = _verdict_with_rung(CleanupLadderRung.QUARANTINE)

    outcome = execute_planning_ladder_rung(
        tmp_path,
        verdict,
        ledger=ledger,
        target="release",
        expected_target_sha="a" * 40,
    )

    assert outcome is result
    assert len(calls) == 1
    rung, kwargs = calls[0]
    assert rung is CleanupLadderRung.QUARANTINE
    assert kwargs["ledger"] is ledger
    assert kwargs["target"] == "release"
    assert kwargs["expected_target_sha"] == "a" * 40
    assert kwargs["in_progress_kind"] is InProgressKind.NONE


def test_execute_planning_ladder_rung_without_a_rung_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    def _boom(rung: CleanupLadderRung, **kwargs: object) -> LadderStepResult:
        raise AssertionError(f"run_ladder_rung must not run for {rung!r} / {kwargs!r}")

    monkeypatch.setattr(integration_owner_seam, "run_ladder_rung", _boom)
    ledger = AttemptLedger(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert execute_planning_ladder_rung(tmp_path, _verdict_with_rung(None), ledger=ledger) is None


def test_execute_planning_ladder_rung_forwards_the_observation_operation_kind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[object] = []

    def _fake_run_ladder_rung(rung: CleanupLadderRung, **kwargs: object) -> LadderStepResult:
        del rung
        seen.append(kwargs["in_progress_kind"])
        return LadderStepResult(CleanupLadderRung.LAST_RESORT, "failed", "failed:test")

    monkeypatch.setattr(integration_owner_seam, "run_ladder_rung", _fake_run_ladder_rung)
    ledger = AttemptLedger(tmp_path, ledger_path=_ledger_path(tmp_path))
    verdict = _verdict_with_rung(CleanupLadderRung.LAST_RESORT)
    verdict = PlanningSyncVerdict(
        action=verdict.action,
        observation=Observation(
            target_vs_feature=TargetVsFeature.EQUAL,
            target_vs_remote=TargetVsRemote.EQUAL,
            worktree=WorktreeState.TRACKED_CHANGES,
            head=HeadState.ATTACHED,
            in_progress_kind=InProgressKind.MERGE,
            in_progress_ownership=InProgressOwnership.OWNED,
            record=IntegrationRecordState.NONE,
            agent_active=False,
        ),
        worktree=verdict.worktree,
        dirt_shape=verdict.dirt_shape,
        in_sync=verdict.in_sync,
        may_plan=verdict.may_plan,
        reason=verdict.reason,
        ladder_rung=verdict.ladder_rung,
    )

    outcome = execute_planning_ladder_rung(tmp_path, verdict, ledger=ledger)

    assert seen == [InProgressKind.MERGE]
    assert outcome is not None
    assert outcome.outcome == "failed"


def test_planning_seam_reaches_rung6_only_as_an_explicitly_disabled_reset(
    tmp_path: Path,
) -> None:
    ledger = AttemptLedger(tmp_path, ledger_path=_ledger_path(tmp_path))
    git = _FakePlumbing()

    outcome = execute_planning_ladder_rung(
        tmp_path,
        _verdict_with_rung(CleanupLadderRung.LAST_RESORT),
        ledger=ledger,
        git=git,
        fs=_FakeQuarantineFs(),
        state_dir=tmp_path / ".agent",
    )

    assert outcome is not None
    assert outcome.outcome == "failed"
    assert outcome.reason == LAST_RESORT_DISABLED_NO_SIGNATURE
    assert git.calls == []
