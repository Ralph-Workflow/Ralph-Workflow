"""The composed observed-log case for tracked residue under a moving target.

AC-10 composed case: the halves live separately in
:mod:`tests.test_integration_cleanup` (residue saved to a backup ref and
restored) and :mod:`tests.test_integration_planning_sync_seam` (the
planning gate's typed verdict). This file chains the WHOLE observed
sequence against the production machinery with fakes only — no real
git, no subprocess, no sleep:

1. planning dispatches and leaves TRACKED residue while ``<target>``
   advances concurrently (the observation reads BEHIND);
2. the residue executor charges through the REAL decision table and
   owner ledger seam, preserves the residue in a backup ref and
   restores the tree — the typed outcome names the action, the ref and
   the restored paths;
3. the planning-sync seam re-decides on the now-clean tree with the
   target ahead: planning stays held, integration re-runs first (the
   rebase-first ladder) and NO cleanup-ladder rung dispatches;
4. integration converges (target contained): planning starts;
5. negatively asserted across the whole composed run: the resolver is
   NEVER dispatched for the dirt — no ``DISPATCH_RESOLVER`` decision,
   no ``RESOLVE_ACTIVE_OPERATION`` seam action. A regression that
   dispatched a resolver for residue dirt fails this test.
"""

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
    """Director stand-in serving the observation the test installed."""

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
    """Install the recording director and the canned porcelain probe."""
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
    """The composed observed case, end to end, with the negative resolver assertion."""
    ledger_path = tmp_path / ".agent" / "observed-case-ledger.json"

    # -- Step 1: tracked residue while <target> is ahead. -------------------
    # The pre-dispatch baseline is clean; planning then dirties a tracked
    # path. The target advancing concurrently is what the observation
    # will report as BEHIND in step 3.
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
    # Typed outcome, charged through the REAL table + owner ledger seam:
    # the dirt earned SAVE_AND_RESTORE — and would have been REFUSED (not
    # silently run) had the table answered anything outside the cleanup
    # ladder, so a resolver regression fails right here.
    assert not residue_outcome.refused
    assert residue_outcome.action is NextAction.SAVE_AND_RESTORE
    # Residue preserved in a backup ref BEFORE the restore:
    assert residue_outcome.backup_ref == "refs/ralph-backup/fake-01"
    assert residue_outcome.backup_ref in git.existing_refs
    # ... and the tree is clean again:
    assert residue_outcome.restored_paths == ("src/app.py",)
    assert residue_outcome.remaining_paths == ()
    assert git.worktree["src/app.py"] == "base"
    assert len(warns) == 1
    assert "src/app.py" in warns[0]
    assert "refs/ralph-backup/fake-01" in warns[0]

    # -- Steps 2-3: the planning-sync seam on the cleaned tree. -------------
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
    # The tree is clean but the branch does not contain the target tip:
    # planning stays held and the rebase-first ladder answers.
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

    # Integration converges: the feature now contains the target tip.
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

    # -- Step 4: the negative assertion. ------------------------------------
    # Across the whole composed run the dirt was answered by exactly one
    # cleanup-ladder action (SAVE_AND_RESTORE, asserted above) and the
    # seam actions were reintegrate-then-land. A resolver dispatched for
    # the dirt — at either layer — fails this test.
    assert IntegrationAction.RESOLVE_ACTIVE_OPERATION not in (held.action, started.action)
    assert NextAction.DISPATCH_RESOLVER not in (
        residue_outcome.action,
        held.action,
        started.action,
    )
    assert ladder_calls == [], "no cleanup-ladder rung dispatched for the clean-tree verdicts"
