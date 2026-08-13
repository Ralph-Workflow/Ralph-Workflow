"""Regression coverage for single-agent broken-agent recovery."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.agent_retry_intent import AgentRetryIntent
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


def _minimal_policy_bundle() -> PolicyBundle:
    with tempfile.TemporaryDirectory() as directory:
        return load_policy(Path(directory) / ".agent")


def test_broken_agent_regression_single_agent_chain_enters_wait_state() -> None:
    """S-2: a broken sole agent waits for cooldown instead of looping through recovery."""
    policy_bundle = _minimal_policy_bundle()
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=FakeClock(start=0.0),
            policy_bundle=policy_bundle,
        )
    )
    state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(agents=["claude"], current_index=0, retries=0),
        },
        agent_retry_intent=AgentRetryIntent(
            failure_reason="BrokenAgentExitError",
            skip_same_agent_retries=True,
            failed_agent_name="claude",
            broken_agent_reason="no_output",
        ),
    ).copy_with(last_connectivity_state="online")

    new_state, effects = reduce(
        state,
        PipelineEvent.AGENT_FAILURE,
        pipeline_policy=policy_bundle.pipeline,
        recovery=controller,
    )

    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert new_state.phase == "development"
    assert chain.current_index == 0
    assert new_state.is_waiting_state is True
    assert new_state.last_retry_delay_ms > 0
    assert effects == []
