"""AC-10 required test: residue plus restore failure ends at rung 3/4.

The criterion (``PRODUCT_CRITERIA.md``, dirty-tree section, required
tests): "residue plus restore failure ends in the fallback ladder
reaching rung 3/4 with no further resolver dispatch for the dirt".

The neighbours cover the halves: ``test_integration_dirt_edge_cases``
proves an interrupted restore persists resumable progress and never
takes a second backup, and ``test_integration_dirt_decisions`` climbs
the rungs against a synthetic trace. Neither pins the criterion's
specific combination — a residue whose restore KEEPS failing, charged
through the real decision table, must land on the QUARANTINE (rung 3)
or FRESH_WORKTREE (rung 4) rung with zero resolver dispatches.

Everything is fake-driven (``_FakeCleanupGit`` / real decision table
over a tmp-file ledger): no git subprocess, no sleep, no wall clock.
"""

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
    """Residue ledger port serving one pre-decided action per charge."""

    def __init__(self, action: NextAction) -> None:
        self._action = action

    def charge_residue_attempt(self, root: Path, *, residue: PlanningResidue) -> NextAction:
        del root, residue
        return self._action


def test_residue_plus_restore_failure_reaches_rung_3_or_4_without_resolver(
    tmp_path: Path,
) -> None:
    """A restore that keeps failing escalates to rung 3/4; no resolver runs.

    The residue is tracked planning residue; the restore fails on every
    attempt (``fail_restore`` models the file-locked / permissions
    failure the criterion names), so the tree stays dirty and the same
    evidence recurs. Each recurrence is charged through the real
    decision table into the durable attempt ledger. The criterion's
    bound: by the third or fourth cleanup attempt the ladder reaches
    QUARANTINE (rung 3) or FRESH_WORKTREE (rung 4) — and at NO depth is
    a resolver dispatched for the dirt.
    """
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
        # The rung's executor runs against the tree; the restore fails
        # every time, so the residue recurs at the next pass.
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
    # The work is never discarded: the backup ref holds the residue.
    assert git.existing_refs, "the residue was not preserved before any restore"
