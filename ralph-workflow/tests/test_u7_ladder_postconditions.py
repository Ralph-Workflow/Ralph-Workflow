from pathlib import Path

from ralph.pipeline._integration_ladder_seams import RungPostCondition
from ralph.pipeline.integration_decision import CleanupLadderRung
from ralph.pipeline.integration_ladder import run_ladder_rung
from tests._fake_git_clock import FakeClock
from tests._integration_ladder_fakes import _FakePlumbing, _FakeQuarantineFs, _ledger


def test_ladder_postcondition_regression_unchanged_action_fails_and_escalates(
    tmp_path: Path,
) -> None:
    result = run_ladder_rung(
        CleanupLadderRung.QUARANTINE,
        root=tmp_path,
        ledger=_ledger(tmp_path),
        git=_FakePlumbing(),
        fs=_FakeQuarantineFs(),
        clock=FakeClock(),
        paths=("blocked",),
        verify_post_condition=lambda _condition: False,
    )

    assert result.outcome == "failed"
    assert result.next_rung is CleanupLadderRung.FRESH_WORKTREE
    assert result.post_condition is RungPostCondition.BLOCKING_OBSERVATION_CHANGED
    assert not result.post_condition_verified


def test_ladder_postcondition_regression_verified_action_succeeds(tmp_path: Path) -> None:
    result = run_ladder_rung(
        CleanupLadderRung.QUARANTINE,
        root=tmp_path,
        ledger=_ledger(tmp_path),
        git=_FakePlumbing(),
        fs=_FakeQuarantineFs(),
        clock=FakeClock(),
        paths=("blocked",),
        verify_post_condition=lambda condition: (
            condition is RungPostCondition.BLOCKING_OBSERVATION_CHANGED
        ),
    )

    assert result.outcome == "succeeded"
    assert result.post_condition_verified
