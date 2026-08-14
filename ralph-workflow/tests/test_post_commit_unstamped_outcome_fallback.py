"""Post-commit routing when a cycle reaches its commit with no recorded verdict.

`post_commit_routes` match on the cycle outcome, and many routes into the
final-commit path never record one (agent-chain fallbacks, a resumed legacy
checkpoint, a result-status override, an analysis phase that succeeded without
emitting a decision). Falling through the route table in that case silently
ended the whole run while the cycle budget still had room, so an unrecorded
verdict is now resolved to the phase's declared forward outcome instead of
skipping the table.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline.handoffs import resolve_post_commit_phase
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def _policy() -> PipelinePolicy:
    return load_policy(_DEFAULTS_DIR).pipeline


def _final_commit_state(*, spent: int, outcome: str | None) -> PipelineState:
    return PipelineState(
        phase="development_final_commit",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": spent},
        pending_cycle_outcome=outcome,
    )


def test_unrecorded_outcome_with_budget_left_starts_the_next_cycle() -> None:
    """No verdict must not end a run that still has dev cycles left."""
    assert resolve_post_commit_phase(_final_commit_state(spent=1, outcome=None), _policy()) == (
        "planning"
    )


def test_unrecorded_outcome_with_budget_spent_ends_the_run() -> None:
    """No verdict still terminates once the cycle budget is gone."""
    assert resolve_post_commit_phase(_final_commit_state(spent=5, outcome=None), _policy()) == (
        "complete"
    )


def test_recorded_failure_outcome_still_wins() -> None:
    """An explicitly recorded verdict is never overridden by the fallback."""
    assert resolve_post_commit_phase(_final_commit_state(spent=5, outcome="failed"), _policy()) == (
        "failed_terminal"
    )


def test_commit_phase_without_declared_routes_keeps_its_success_transition() -> None:
    """The intermediate commit declares no routes and must still advance normally."""
    state = PipelineState(
        phase="development_commit",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        pending_cycle_outcome=None,
    )

    assert resolve_post_commit_phase(state, _policy()) == "development_analysis"
