"""The recovery cycle cap must bound technical failures, not just agent ones.

``CycleCap`` documents itself as the guard against "a persistently-failing
handler looping silently forever", and ``recovery.cycle_cap`` is counted for
every category. But the check lived only at the end of ``handle``'s AGENT
branch, and the technical categories -- ENVIRONMENTAL, ARTIFACT_VALIDATION and
AMBIGUOUS -- return before reaching it. Nothing else bounded them:
``_enter_phase_failed`` routes to ``failed_route``, whose recovery hop
re-enters ``previous_phase`` and resets that chain's retry counter, so the
only counter that bounded a pass was wiped on every pass.

A ``TypeError`` raised by a wrapper on the commit path classifies AMBIGUOUS
(``counts_against_budget=False``), which is how one signature bug turned into
``development_commit`` -> ``failed_terminal`` -> ``development_commit`` at
roughly ten iterations a second, forever, with no commit ever produced.

These tests pin that every category is bounded by the same cap.
"""

from __future__ import annotations

from pathlib import Path

from ralph.pipeline.effects import ExitFailureEffect
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.controller import (
    FailureContext,
    RecoveryController,
    RecoveryControllerOptions,
)

_CAP = 3
_TECHNICAL_RETRY_CAP = 10
#: Generous ceiling on ``handle`` calls: an unbounded loop hits it, a bounded
#: one exits after roughly ``_CAP * (_TECHNICAL_RETRY_CAP + 1)`` calls.
_HANDLE_CALL_CEILING = _CAP * (_TECHNICAL_RETRY_CAP + 2) * 3


def _policy_bundle() -> object:
    return load_policy(Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults")


def _controller() -> RecoveryController:
    registry = AgentBudgetRegistry().set_budget("development_commit", "claude", max_retries=1)
    return RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=_CAP,
            budget_registry=registry,
            policy_bundle=_policy_bundle(),
        )
    )


def _fresh_state() -> PipelineState:
    return PipelineState(
        phase="development_commit",
        phase_chains={
            "development_commit": AgentChainState(
                agents=["claude"], current_index=0, retries=0
            )
        },
    )


def _drive_until_exit(failure: BaseException) -> tuple[int, PipelineState, list[object]]:
    """Replay the observed loop and report how many outer rounds it took.

    One outer round is: retry the phase in-chain until the technical retry cap
    is spent, land on ``failed_terminal``, then re-enter the phase with a fresh
    chain -- which is exactly what the failed-route recovery hop in
    ``runner.py`` does via ``reset_phase_chain_for_recovery``. Wiping the chain
    is what made the retry counter useless as a bound, so the driver has to
    reproduce it or the test proves nothing.

    Returns the outer-round count, the final state, and the last effects.
    """
    controller = _controller()
    state = _fresh_state()
    rounds = 0
    for _ in range(_HANDLE_CALL_CEILING):
        state, effects, _ = controller.handle(
            state,
            failure,
            FailureContext(phase="development_commit", agent="claude"),
        )
        if any(isinstance(item, ExitFailureEffect) for item in effects):
            return rounds + 1, state, list(effects)
        if state.phase == "failed_terminal":
            rounds += 1
            state = state.copy_with(
                phase="development_commit",
                previous_phase="failed_terminal",
                phase_chains={
                    "development_commit": AgentChainState(
                        agents=["claude"], current_index=0, retries=0
                    )
                },
            )
    return rounds, state, []


def test_ambiguous_failure_loop_is_bounded_by_the_cycle_cap() -> None:
    """The exact production shape: a TypeError from a wrapper on the commit path."""
    failure = TypeError(
        "ralph.pipeline.commit_executor.execute_commit_effect() got multiple "
        "values for keyword argument 'has_residual_work_fn'"
    )

    rounds, _, effects = _drive_until_exit(failure)

    assert effects, (
        "an ambiguous failure re-entered its phase forever; the cycle cap never fired"
    )
    assert rounds == _CAP
    exits = [item for item in effects if isinstance(item, ExitFailureEffect)]
    assert exits, "the bounded loop must terminate with an ExitFailureEffect"
    assert "recovery cycle cap" in exits[0].reason
    assert "ambiguous" in exits[0].reason


def test_the_cap_fires_on_the_cycle_that_reaches_it() -> None:
    """Not merely bounded -- bounded at the configured cap."""
    rounds, _, effects = _drive_until_exit(RuntimeError("something went wrong but not sure what"))

    assert rounds == _CAP
    assert effects


def test_the_failing_phase_is_still_routed_before_the_run_exits() -> None:
    """The exit effect says why the run stopped; the state must say where."""
    _, state, effects = _drive_until_exit(RuntimeError("unclassifiable"))

    assert effects
    assert state.phase == "failed_terminal"
    assert state.recovery_cycle_count == _CAP
