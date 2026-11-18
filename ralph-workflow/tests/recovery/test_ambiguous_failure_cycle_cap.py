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

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_ambiguous_failure_loop_is_bounded_by_the_cycle_cap": "operator rule: an agent is either on cooldown or not; a cooled sole agent now waits instead of entering the failed phase, so the cycle cap is driven through a chain with no agents, the only route that still reaches the failed phase",
    "test_the_cap_fires_on_the_cycle_that_reaches_it": "operator rule: an agent is either on cooldown or not; a cooled sole agent now waits instead of entering the failed phase, so the cycle cap is driven through a chain with no agents, the only route that still reaches the failed phase",
    "test_the_failing_phase_is_still_routed_before_the_run_exits": "operator rule: an agent is either on cooldown or not; a cooled sole agent now waits instead of entering the failed phase, so the failed route is driven through a chain with no agents, the only route that still reaches the failed phase",
    "test_ambiguous_failures_on_a_sole_agent_end_in_a_cooldown_wait": "operator rule: an agent is either on cooldown or not; pins that a sole agent failing ambiguously does not spin but reaches the all-agents-unavailable wait with a finite delay",
}


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


def _fresh_state(agents: tuple[str, ...] = ()) -> PipelineState:
    return PipelineState(
        phase="development_commit",
        phase_chains={
            "development_commit": AgentChainState(agents=list(agents), current_index=0, retries=0)
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
            FailureContext(phase="development_commit", agent=None),
        )
        if any(isinstance(item, ExitFailureEffect) for item in effects):
            return rounds + 1, state, list(effects)
        if state.phase == "failed_terminal":
            rounds += 1
            state = state.copy_with(
                phase="development_commit",
                previous_phase="failed_terminal",
                phase_chains={
                    "development_commit": AgentChainState(agents=[], current_index=0, retries=0)
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


def test_ambiguous_failures_on_a_sole_agent_end_in_a_cooldown_wait() -> None:
    controller = _controller()
    state = _fresh_state(("claude",))
    for _ in range(_HANDLE_CALL_CEILING):
        state, effects, _ = controller.handle(
            state,
            RuntimeError("unclassifiable"),
            FailureContext(phase="development_commit", agent="claude"),
        )
        assert effects == []
        if state.is_waiting_state:
            break

    assert state.is_waiting_state is True
    assert state.phase == "development_commit"
    assert state.last_retry_delay_ms > 0
    assert state.recovery_cycle_count == 0
