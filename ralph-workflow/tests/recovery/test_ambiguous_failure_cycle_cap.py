from __future__ import annotations

from pathlib import Path

from ralph.pipeline.effects import ExitFailureEffect
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.controller import (
    FailureContext,
    RecoveryController,
    RecoveryControllerOptions,
)

_CAP = 3
_CONSECUTIVE_UNPRODUCTIVE_LIMIT = 3
_TOTAL_UNPRODUCTIVE_LIMIT = 9
_HANDLE_CALL_CEILING = _CAP * (_CONSECUTIVE_UNPRODUCTIVE_LIMIT + 2) * 3


def _policy_bundle() -> object:
    return load_policy(Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults")


def _controller() -> RecoveryController:
    return RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=_CAP,
            policy_bundle=_policy_bundle(),
            agent_max_consecutive_unproductive=_CONSECUTIVE_UNPRODUCTIVE_LIMIT,
            agent_max_total_unproductive=_TOTAL_UNPRODUCTIVE_LIMIT,
        )
    )


def _fresh_state() -> PipelineState:
    return PipelineState(
        phase="development_commit",
        phase_chains={
            "development_commit": AgentChainState(agents=["claude"], current_index=0, retries=0)
        },
    )


def _drive_until_exit(failure: BaseException) -> tuple[int, PipelineState, list[object]]:
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
    failure = TypeError(
        "ralph.pipeline.commit_executor.execute_commit_effect() got multiple "
        "values for keyword argument 'has_residual_work_fn'"
    )

    rounds, _, effects = _drive_until_exit(failure)

    assert effects, "an ambiguous failure re-entered its phase forever; the cycle cap never fired"
    assert rounds == _CAP
    exits = [item for item in effects if isinstance(item, ExitFailureEffect)]
    assert exits, "the bounded loop must terminate with an ExitFailureEffect"
    assert "recovery cycle cap" in exits[0].reason
    assert "ambiguous" in exits[0].reason


def test_the_cap_fires_on_the_cycle_that_reaches_it() -> None:
    rounds, _, effects = _drive_until_exit(RuntimeError("something went wrong but not sure what"))

    assert rounds == _CAP
    assert effects


def test_the_failing_phase_is_still_routed_before_the_run_exits() -> None:
    _, state, effects = _drive_until_exit(RuntimeError("unclassifiable"))

    assert effects
    assert state.phase == "failed_terminal"
    assert state.recovery_cycle_count == _CAP
