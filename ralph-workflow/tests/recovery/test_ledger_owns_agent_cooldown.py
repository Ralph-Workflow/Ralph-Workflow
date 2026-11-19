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
from ralph.recovery.agent_failure_ledger import Cooldown, Eligible
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_agent_that_keeps_changing_the_workspace_cools_down_only_at_the_total_limit (renamed)": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_agent_that_keeps_changing_the_workspace_is_demoted_only_at_the_total_limit with an unchanged body"
    ),
    "test_agent_that_changes_nothing_cools_down_at_the_consecutive_limit": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_agent_that_changes_nothing_is_demoted_at_the_consecutive_limit with an unchanged body"
    ),
    "test_cooldown_is_logged_with_the_phase_agent_and_counts (renamed)": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_demotion_is_logged_with_the_phase_agent_and_counts with an unchanged body"
    ),
    "test_ledger_owns_agent_cooldown module": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; module renamed from tests/recovery/test_ledger_owns_agent_demotion.py with unchanged content"
    ),
    "test_agent_that_keeps_changing_the_workspace_cools_down_only_at_the_total_limit": "operator rule: an agent is either on cooldown or not; the total limit now starts an expiring unproductive_limit cooldown instead of a permanent Spent state, so the test asserts the cooldown reason and that the agent is preferred again once it expires",
    "test_cooldown_is_logged_with_the_phase_agent_and_counts": "operator rule: an agent is either on cooldown or not; the demotion log and the consecutive_unproductive_limit_reached reason no longer exist, so the test asserts the cooldown_decision log line with reason unproductive_limit and the unproductive counts",
}

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


def _controller(agents: list[str], clock: FakeClock | None = None) -> RecoveryController:
    return RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=50,
            clock=clock or FakeClock(start=0.0),
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


def test_agent_that_keeps_changing_the_workspace_cools_down_only_at_the_total_limit() -> None:
    clock = FakeClock(start=0.0)
    controller = _controller([_AGENT_A, _AGENT_B], clock)
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
    cooled = controller.ledger.status(_PHASE, _AGENT_A)
    assert isinstance(cooled, Cooldown), cooled
    assert cooled.reason == str(UnavailabilityReason.UNPRODUCTIVE_LIMIT)
    assert isinstance(controller.ledger.status(_PHASE, _AGENT_B), Eligible)
    now_ms = int(clock.monotonic() * 1000)
    assert cooled.until_ms > now_ms
    clock.advance((cooled.until_ms - now_ms) / 1000.0)
    assert controller.preferred_agent_index(_PHASE, [_AGENT_A, _AGENT_B]).agent == _AGENT_A


def test_agent_that_changes_nothing_cools_down_at_the_consecutive_limit() -> None:
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


def test_cooldown_is_logged_with_the_phase_agent_and_counts() -> None:
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
    assert "cooldown_decision" in log_output
    assert f"agent={_AGENT_A}" in log_output
    assert f"phase={_PHASE}" in log_output
    assert "reason=unproductive_limit" in log_output
    assert "unproductive_attempts consecutive=3 total=3" in log_output
    assert "demoted" not in log_output.lower()


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
