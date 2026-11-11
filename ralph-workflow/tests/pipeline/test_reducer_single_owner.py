from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.events import PhaseFailureEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.models import (
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    RecoveryPolicy,
)
from tests.recovery._scenario_harness import (
    AgentAttemptResult,
    run_scenario,
)

if TYPE_CHECKING:
    from pathlib import Path


def _policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": PhaseDefinition(
                drain="development",
                role="terminal",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "failed_terminal": PhaseDefinition(
                drain="development",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )


def test_unproductive_agent_runs_three_times_then_the_next_agent_runs(tmp_path: Path) -> None:

    def script(agent: str, _phase: str, _attempt: int, _workspace: object) -> AgentAttemptResult:
        if agent == "agent_b":
            return AgentAttemptResult(produce_artifact=True)
        return AgentAttemptResult(produce_artifact=False)

    outcome = run_scenario(
        tmp_path,
        script=script,
        agents=("agent_a", "agent_b"),
        max_iterations=15,
    )

    assert outcome.invocations == ["agent_a", "agent_a", "agent_a", "agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0


def test_reducer_without_an_owner_grants_no_further_attempt() -> None:
    state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode"], current_index=0, retries=2
            )
        },
    )
    event = PhaseFailureEvent(
        phase="development",
        reason="missing artifact",
        recoverable=True,
    )

    new_state, effects = reducer_reduce(state, event, _policy())

    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.retries == 2
    assert chain.current_index == 0
    assert new_state.metrics.total_retries == 0
    assert new_state.metrics.total_fallbacks == 0
    assert new_state.phase == "failed_terminal"
    assert new_state.last_error is not None
    assert "no recovery owner is reachable" in new_state.last_error
    assert effects == []
