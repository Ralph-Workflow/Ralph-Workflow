from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import AgentChainConfig
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle

_PHASE = "development"
_AGENT_A = "agent_a"
_AGENT_B = "agent_b"

_CONSECUTIVE_LIMIT = 3
_TOTAL_LIMIT = 9


def _policy_bundle(agents: list[str]) -> PolicyBundle:
    with tempfile.TemporaryDirectory() as tmp:
        bundle = load_policy(Path(tmp) / ".agent")
    chains = dict(bundle.agents.agent_chains)
    chains["development"] = AgentChainConfig(agents=list(agents))
    agents_policy = bundle.agents.model_copy(update={"agent_chains": chains})
    return bundle.model_copy(update={"agents": agents_policy})


def _make_state(agents: list[str]) -> PipelineState:
    return PipelineState(
        phase=_PHASE,
        phase_chains={_PHASE: AgentChainState(agents=list(agents), current_index=0, retries=0)},
    )


def _controller(agents: list[str]) -> RecoveryController:
    return RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=50,
            clock=FakeClock(start=0.0),
            policy_bundle=_policy_bundle(agents),
        )
    )


def _fail(
    controller: RecoveryController,
    state: PipelineState,
    agent: str,
    *,
    workspace_changed: bool,
) -> PipelineState:
    new_state, _effects, _evt = controller.handle(
        state,
        "Missing required artifact",
        FailureContext(phase=_PHASE, agent=agent),
        workspace_changed=workspace_changed,
        artifact_valid=False,
    )
    return new_state


def test_agent_that_keeps_changing_the_workspace_is_demoted_only_at_the_total_limit() -> None:
    controller = _controller([_AGENT_A, _AGENT_B])
    state = _make_state([_AGENT_A, _AGENT_B])

    for attempt in range(1, _TOTAL_LIMIT):
        state = _fail(controller, state, _AGENT_A, workspace_changed=True)

        selection = controller.preferred_agent_index(_PHASE, [_AGENT_A, _AGENT_B])
        assert selection.agent == _AGENT_A, (
            f"attempt {attempt}: the agent changed the workspace, so it must keep the chain; "
            f"got {selection.agent!r}"
        )
        chain = state.chain_for_phase(_PHASE)
        assert chain is not None
        assert chain.current_index == 0

    state = _fail(controller, state, _AGENT_A, workspace_changed=True)

    selection = controller.preferred_agent_index(_PHASE, [_AGENT_A, _AGENT_B])
    assert selection.agent == _AGENT_B, (
        f"the agent must be demoted at the ledger's total limit of {_TOTAL_LIMIT}; "
        f"got {selection.agent!r}"
    )
    chain = state.chain_for_phase(_PHASE)
    assert chain is not None
    assert chain.current_index == 1
    assert controller.ledger.status(_PHASE, _AGENT_A).is_spent is True
    assert controller.ledger.status(_PHASE, _AGENT_B).is_spent is False


def test_agent_that_changes_nothing_is_demoted_at_the_consecutive_limit() -> None:
    controller = _controller([_AGENT_A, _AGENT_B])
    state = _make_state([_AGENT_A, _AGENT_B])

    for attempt in range(1, _CONSECUTIVE_LIMIT):
        state = _fail(controller, state, _AGENT_A, workspace_changed=False)
        selection = controller.preferred_agent_index(_PHASE, [_AGENT_A, _AGENT_B])
        assert selection.agent == _AGENT_A, (
            f"attempt {attempt}: the agent has not reached the consecutive limit yet; "
            f"got {selection.agent!r}"
        )

    state = _fail(controller, state, _AGENT_A, workspace_changed=False)

    selection = controller.preferred_agent_index(_PHASE, [_AGENT_A, _AGENT_B])
    assert selection.agent == _AGENT_B, (
        f"the agent must be demoted after {_CONSECUTIVE_LIMIT} consecutive unproductive "
        f"attempts; got {selection.agent!r}"
    )
    chain = state.chain_for_phase(_PHASE)
    assert chain is not None
    assert chain.current_index == 1


def test_demotion_is_logged_with_the_phase_agent_and_counts() -> None:
    sink = io.StringIO()
    handler_id = logger.add(sink, level="WARNING", format="{level} {message}")
    try:
        controller = _controller([_AGENT_A, _AGENT_B])
        state = _make_state([_AGENT_A, _AGENT_B])

        for _ in range(_CONSECUTIVE_LIMIT):
            state = _fail(controller, state, _AGENT_A, workspace_changed=False)
    finally:
        logger.remove(handler_id)

    log_output = sink.getvalue()
    assert "demoted" in log_output.lower()
    assert _AGENT_A in log_output
    assert _PHASE in log_output
    assert "consecutive=3" in log_output
    assert "total=3" in log_output
    assert "consecutive_unproductive_limit_reached" in log_output


class _AgentInactivityTimeoutError(Exception):
    pass


_AgentInactivityTimeoutError.__name__ = "AgentInactivityTimeoutError"


def test_a_counted_agent_fault_keeps_the_chain_past_the_consecutive_limit() -> None:
    controller = _controller([_AGENT_A, _AGENT_B])
    state = _make_state([_AGENT_A, _AGENT_B])

    for attempt in range(1, _TOTAL_LIMIT):
        new_state, _effects, evt = controller.handle(
            state,
            _AgentInactivityTimeoutError(f"{_AGENT_A} idle"),
            FailureContext(phase=_PHASE, agent=_AGENT_A),
            workspace_changed=True,
            artifact_valid=False,
        )
        state = new_state
        assert evt.counted_against_budget is True, "the fault must be charged against the agent"

        selection = controller.preferred_agent_index(_PHASE, [_AGENT_A, _AGENT_B])
        assert selection.agent == _AGENT_A, (
            f"attempt {attempt}: the agent changed the workspace, so it must keep the chain; "
            f"got {selection.agent!r}"
        )

    new_state, _effects, _evt = controller.handle(
        state,
        _AgentInactivityTimeoutError(f"{_AGENT_A} idle"),
        FailureContext(phase=_PHASE, agent=_AGENT_A),
        workspace_changed=True,
        artifact_valid=False,
    )
    state = new_state

    selection = controller.preferred_agent_index(_PHASE, [_AGENT_A, _AGENT_B])
    assert selection.agent == _AGENT_B, (
        f"the agent must be demoted at the ledger's total limit of {_TOTAL_LIMIT}; "
        f"got {selection.agent!r}"
    )
    chain = state.chain_for_phase(_PHASE)
    assert chain is not None
    assert chain.current_index == 1
