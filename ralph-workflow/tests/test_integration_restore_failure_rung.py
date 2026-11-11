from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline.integration_cleanup import (
    PlanningResidue,
    detect_planning_residue,
    residue_observation,
    run_residue_cleanup,
)
from ralph.pipeline.integration_decision import (
    CleanupLadderRung,
    DecisionContext,
    DirtAttribution,
    NextAction,
    cleanup_action_for,
    decide,
)
from ralph.pipeline.integration_owner import (
    charge_integration_attempt,
    load_integration_attempts,
)
from tests.test_integration_cleanup import (
    _ROOT,
    _baseline,
    _FakeCleanupGit,
)

pytestmark = pytest.mark.timeout_seconds(10)


class _ScriptedLedger:
    def __init__(self, action: NextAction) -> None:
        self._action = action

    def charge_residue_attempt(self, root: Path, *, residue: PlanningResidue) -> NextAction:
        del root, residue
        return self._action


def test_residue_plus_restore_failure_reaches_rung_3_or_4_without_resolver(
    tmp_path: Path,
) -> None:
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.worktree["src/app.py"] = "planning wrote here"
    git.fail_restore = True

    actions: list[NextAction] = []
    for _ in range(4):
        blob = git.porcelain_status_z(_ROOT)
        assert blob is not None
        diff = git.tracked_diff_hash(_ROOT)
        assert diff is not None
        residue = detect_planning_residue(baseline, blob, after_diff_hash=diff)
        assert residue is not None
        observation, shape = residue_observation(residue)
        attempts = load_integration_attempts(tmp_path)
        action = decide(
            DecisionContext(
                observation=observation,
                attempts=attempts,
                dirt_shape=shape,
                dirt_attribution=DirtAttribution.UNOWNED,
            )
        )
        charge_integration_attempt(tmp_path, observation=observation, next_action=action)
        actions.append(action)
        run_residue_cleanup(
            _ROOT,
            baseline=baseline,
            git=git,
            ledger=_ScriptedLedger(action),
            target="main",
        )

    rung_3 = cleanup_action_for(CleanupLadderRung.QUARANTINE)
    rung_4 = cleanup_action_for(CleanupLadderRung.FRESH_WORKTREE)
    assert rung_3 in actions[:4] or rung_4 in actions[:4], (
        f"residue plus restore failure did not reach rung 3/4: "
        f"{[action.name for action in actions]}"
    )
    assert NextAction.DISPATCH_RESOLVER not in actions, (
        "a resolver was dispatched for dirt whose restore failed"
    )
    assert git.existing_refs, "the residue was not preserved before any restore"
