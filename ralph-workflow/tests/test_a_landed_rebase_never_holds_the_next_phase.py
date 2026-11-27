from __future__ import annotations

from ralph.pipeline.auto_integrate_boundary import record_already_landed
from ralph.pipeline.integration_resolution import RebaseOutcomeKind
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.reducer import PhaseDecision, decide_phase_transition

_RACED = RebaseState(
    last_outcome=RebaseOutcomeKind.REBASED,
    fast_forwarded=False,
    last_reason="target advanced concurrently (not an ancestor of feature)",
    last_target="main",
)


def _decide(state: RebaseState) -> PhaseDecision:
    return decide_phase_transition(state, None, integration_record=None, rebase_state=state)


def test_a_raced_rebase_record_holds_the_phase() -> None:
    assert _decide(_RACED) is PhaseDecision.BLOCK_FOR_RECOVERY


def test_once_the_work_has_landed_the_record_no_longer_holds_the_phase() -> None:
    landed = record_already_landed(_RACED)
    assert landed.last_outcome is RebaseOutcomeKind.SKIPPED
    assert landed.last_target == "main"
    assert _decide(landed) is not PhaseDecision.BLOCK_FOR_RECOVERY
