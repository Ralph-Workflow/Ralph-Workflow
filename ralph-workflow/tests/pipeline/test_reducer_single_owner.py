"""Single-owner proof for the reducer's agent-failure route.

PRODUCT_CRITERIA §3.4 (one owner decides an agent is not working) and §6.1
(the public-seam scenario). Two proofs live here:

1. The public-seam scenario: a scripted fake agent A produces nothing three
   times and agent B then succeeds. The assertions are OBSERVABLE only --
   which agents ran, in what order, how the run ended, and what was logged.
   No ledger counter is read or written; the owner's verdict is observed
   through the run itself.

2. The ownerless route fails closed: with no recovery owner reachable the
   reducer grants no further attempt to the failing agent and no fallover to
   another agent. It never counts attempts of its own.
"""

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
    """A policy whose failure route is a declared terminal phase."""
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
    """Agent A produces nothing three times; agent B then runs and the run completes.

    The invocation list is the whole proof: A is attempted exactly three
    times -- the consecutive-unproductive limit the OWNER applies -- and the
    next agent in the chain runs afterwards. The reducer itself grants no
    attempt of its own; the demotion that ends A's run comes from the owner.
    """

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
    """With no owner reachable the reducer fails the phase closed.

    The reducer must not count attempts of its own: the chain's retry
    counter stays where it was, the chain does not advance to another agent,
    and no retry/fallback is booked. The phase reaches the policy's declared
    failure route instead.
    """
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
