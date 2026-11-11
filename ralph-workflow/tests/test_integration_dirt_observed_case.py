from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline import (
    IntegrationAction,
    PlanningSyncVerdict,
    integration_director,
    integration_owner_seam,
)
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.integration_cleanup import OwnerResidueLedgerPort, run_residue_cleanup
from ralph.pipeline.integration_decision import CleanupLadderRung, NextAction
from ralph.pipeline.integration_ladder import LadderStepResult
from ralph.pipeline.integration_observation import (
    HeadState,
    InProgressKind,
    InProgressOwnership,
    IntegrationRecordState,
    Observation,
    Reading,
    TargetVsFeature,
    TargetVsRemote,
    WorktreeState,
)
from tests.test_integration_cleanup import _baseline, _FakeCleanupGit

pytestmark = pytest.mark.timeout_seconds(10)


class _RecordingDirector:
    observation: Observation

    def __init__(
        self,
        root: Path,
        *,
        target: str,
        remote: str,
        agent_active: bool = False,
    ) -> None:
        del root, target, remote, agent_active

    def observe(self) -> Observation:
        return type(self).observation


def _install_seam(
    monkeypatch: pytest.MonkeyPatch,
    porcelain: str,
) -> None:
    monkeypatch.setattr(integration_director, "IntegrationDirector", _RecordingDirector)
    monkeypatch.setattr(
        integration_owner_seam,
        "read_worktree_porcelain",
        lambda _root: Reading.known(porcelain),
    )


def _sync_verdict(tmp_path: Path, ledger_path: Path) -> PlanningSyncVerdict:
    return integration_owner_seam.decide_planning_sync(tmp_path, ledger_path=ledger_path)


def test_observed_case_tracked_residue_moving_target_no_resolver_for_dirt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / ".agent" / "observed-case-ledger.json"

    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.worktree["src/app.py"] = "planning wrote here"

    warns: list[str] = []
    residue_outcome = run_residue_cleanup(
        tmp_path,
        baseline=baseline,
        git=git,
        ledger=OwnerResidueLedgerPort(),
        target="main",
        warn=warns.append,
    )
    assert not residue_outcome.refused
    assert residue_outcome.action is NextAction.SAVE_AND_RESTORE
    assert residue_outcome.backup_ref == "refs/ralph-backup/fake-01"
    assert residue_outcome.backup_ref in git.existing_refs
    assert residue_outcome.restored_paths == ("src/app.py",)
    assert residue_outcome.remaining_paths == ()
    assert git.worktree["src/app.py"] == "base"
    assert len(warns) == 1
    assert "src/app.py" in warns[0]
    assert "refs/ralph-backup/fake-01" in warns[0]

    _install_seam(monkeypatch, porcelain="")
    clean_tree = Observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.CLEAN,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.NONE,
        agent_active=False,
    )
    _RecordingDirector.observation = clean_tree

    ladder_calls: list[object] = []

    def _recording_rung(rung: CleanupLadderRung, **kwargs: object) -> LadderStepResult:
        del kwargs
        ladder_calls.append(rung)
        return LadderStepResult(rung, "succeeded", "succeeded:test")

    monkeypatch.setattr(integration_owner_seam, "run_ladder_rung", _recording_rung)

    held = _sync_verdict(tmp_path, ledger_path)
    assert held.in_sync is False
    assert held.may_plan is False
    assert held.worktree.is_clean
    assert held.action is IntegrationAction.REINTEGRATE_AGAINST_TARGET
    assert held.ladder_rung is None, "a clean tree is not a cleanup-ladder decision"
    assert "target" in held.reason
    assert (
        integration_owner_seam.execute_planning_ladder_rung(
            tmp_path,
            held,
            ledger=AttemptLedger(tmp_path, ledger_path=ledger_path),
        )
        is None
    )

    _RecordingDirector.observation = Observation(
        target_vs_feature=TargetVsFeature.EQUAL,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.CLEAN,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.NONE,
        agent_active=False,
    )
    started = _sync_verdict(tmp_path, ledger_path)
    assert started.in_sync is True
    assert started.may_plan is True, "planning starts only after the tree is clean and in sync"
    assert started.action is IntegrationAction.LAND_TARGET
    assert started.ladder_rung is None

    assert IntegrationAction.RESOLVE_ACTIVE_OPERATION not in (held.action, started.action)
    assert NextAction.DISPATCH_RESOLVER not in (
        residue_outcome.action,
        held.action,
        started.action,
    )
    assert ladder_calls == [], "no cleanup-ladder rung dispatched for the clean-tree verdicts"
