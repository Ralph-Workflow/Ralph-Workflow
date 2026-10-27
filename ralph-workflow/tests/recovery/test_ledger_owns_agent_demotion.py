"""Behavioural proof: the failure ledger alone decides when an agent is demoted.

The failure ledger is the single owner of agent health. It applies two
limits to the unproductive attempts an agent makes in a phase:

- the CONSECUTIVE limit (default 3) demotes an agent that produces no
  artifact AND changes nothing, and
- the TOTAL limit (default 9) demotes an agent that has produced no valid
  artifact that many times, whether or not it changed the workspace.

A workspace change therefore resets only the CONSECUTIVE count: an agent
that keeps editing files without ever producing the artifact still reaches
the TOTAL limit, and it is NOT demoted at the CONSECUTIVE limit.

These tests drive the controller through its PUBLIC interface only
(``RecoveryController.handle``, ``RecoveryController.preferred_agent_index``,
``RecoveryController.ledger``). They assert observable effects: which agent
runs next, the demotion log, and the end state. No private attribute and no
serialised counter is read.
"""

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

#: The ledger's consecutive unproductive limit (the default).
_CONSECUTIVE_LIMIT = 3
#: The ledger's total unproductive limit (the default).
_TOTAL_LIMIT = 9


def _policy_bundle(agents: list[str]) -> PolicyBundle:
    """Build a policy whose development chain carries exactly ``agents``."""
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
    """Drive one unproductive attempt for ``agent`` through the controller."""
    new_state, _effects, _evt = controller.handle(
        state,
        "Missing required artifact",
        FailureContext(phase=_PHASE, agent=agent),
        workspace_changed=workspace_changed,
        artifact_valid=False,
    )
    return new_state


def test_agent_that_keeps_changing_the_workspace_is_demoted_only_at_the_total_limit() -> None:
    """A workspace change on every attempt keeps the agent selected for EIGHT attempts.

    The agent produces an invalid artifact every time but changes the
    workspace each time, so the ledger's CONSECUTIVE count is reset on every
    attempt and only the TOTAL count grows. The agent therefore keeps the
    chain for eight attempts and is demoted on the ninth, when the ledger's
    TOTAL limit (9) is reached -- it is NOT demoted at the CONSECUTIVE
    limit (3).
    """
    controller = _controller([_AGENT_A, _AGENT_B])
    state = _make_state([_AGENT_A, _AGENT_B])

    for attempt in range(1, _TOTAL_LIMIT):
        state = _fail(controller, state, _AGENT_A, workspace_changed=True)

        selection = controller.preferred_agent_index(_PHASE, [_AGENT_A, _AGENT_B])
        assert selection.agent == _AGENT_A, (
            f"attempt {attempt}: the agent changed the workspace, so it must keep the chain; "
            f"got {selection.agent!r}"
        )
        # The chain still points at the agent that has not been demoted yet.
        chain = state.chain_for_phase(_PHASE)
        assert chain is not None
        assert chain.current_index == 0

    # The ninth attempt reaches the ledger's TOTAL limit: the agent is spent
    # and the chain moves to the next agent.
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
    """The complementary case: an invalid artifact with NO workspace change.

    The agent changes nothing, so the ledger's CONSECUTIVE count grows on
    every attempt. It is demoted after three consecutive unproductive
    attempts and the next agent runs.
    """
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
    """The demotion is reported with its phase, agent, counts, and reason."""
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
    """Exception whose class name makes the classifier read it as an agent fault."""


_AgentInactivityTimeoutError.__name__ = "AgentInactivityTimeoutError"


def test_a_counted_agent_fault_keeps_the_chain_past_the_consecutive_limit() -> None:
    """A counted agent fault that changes the workspace keeps the chain for EIGHT attempts.

    This is the case a per-agent budget allowance used to break: the fault
    IS charged against the agent, but a workspace change resets the ledger's
    CONSECUTIVE count, so only the TOTAL count grows. The agent therefore
    keeps the chain for eight attempts and is demoted on the ninth, when the
    ledger's TOTAL limit (9) is reached.
    """
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

    # The ninth attempt reaches the ledger's TOTAL limit.
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
