from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import AgentChainConfig, AgentDrainConfig
from ralph.recovery.controller import FailureContext, RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEventBus, FalloverEvent


def _minimal_policy_bundle(agents: list[str] | None = None) -> object:
    with tempfile.TemporaryDirectory() as d:
        bundle = load_policy(Path(d) / ".agent")
        if agents is None:
            return bundle
        agents_policy = bundle.agents.model_copy(
            update={
                "agent_chains": {"default_chain": AgentChainConfig(agents=list(agents))},
                "agent_drains": {"development": AgentDrainConfig(chain="default_chain")},
            }
        )
        return bundle.model_copy(update={"agents": agents_policy})


_MIN_ERROR_LEN = 10


class _AgentInactivityTimeoutError(Exception):
    pass


_AgentInactivityTimeoutError.__name__ = "AgentInactivityTimeoutError"


def _make_state(agents: list[str]) -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=agents, current_index=0, retries=0)},
    )


_CONSECUTIVE_UNPRODUCTIVE_LIMIT = 3


def _fail_repeatedly(
    controller: RecoveryController,
    state: PipelineState,
    agent: str,
    attempts: int,
) -> PipelineState:
    for _attempt in range(attempts):
        state, _, _ = controller.handle(
            state,
            _AgentInactivityTimeoutError(f"{agent} timed out"),
            FailureContext(phase="development", agent=agent),
        )
    return state


def test_ledger_demotion_advances_the_chain_to_the_next_agent() -> None:
    fallovers: list[FalloverEvent] = []
    bus = FailureEventBus()
    bus.subscribe(lambda evt: fallovers.append(evt) if isinstance(evt, FalloverEvent) else None)

    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            event_bus=bus,
            policy_bundle=_minimal_policy_bundle(["claude", "opencode"]),
        )
    )
    state = _make_state(["claude", "opencode"])

    state = _fail_repeatedly(controller, state, "claude", _CONSECUTIVE_UNPRODUCTIVE_LIMIT)

    assert len(fallovers) == 1
    assert fallovers[0].from_agent == "claude"
    assert fallovers[0].to_agent == "opencode"
    assert state.phase == "development"
    assert state.chain_for_phase("development").current_index == 1
    assert len(state.fallover_history) == 1


def test_every_demoted_agent_makes_the_controller_wait_instead_of_failing() -> None:
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            policy_bundle=_minimal_policy_bundle(["claude", "opencode"]),
        )
    )
    state = _make_state(["claude", "opencode"])

    state = _fail_repeatedly(controller, state, "claude", _CONSECUTIVE_UNPRODUCTIVE_LIMIT)
    state = _fail_repeatedly(controller, state, "opencode", _CONSECUTIVE_UNPRODUCTIVE_LIMIT)

    assert state.phase == "development"
    assert state.is_waiting_state is True
    assert state.last_retry_delay_ms > 0
    assert len(state.fallover_history) == 1


def test_chain_exhaustion_last_error_is_non_sentinel() -> None:
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, policy_bundle=_minimal_policy_bundle())
    )
    state = _make_state(["claude"])

    state = _fail_repeatedly(controller, state, "claude", _CONSECUTIVE_UNPRODUCTIVE_LIMIT)

    assert state.phase == "failed_terminal"
    assert state.last_error is not None
    assert state.last_error not in ("Unknown failure", "unknown failure", "None", "null", "")
    assert len(state.last_error) > _MIN_ERROR_LEN


def test_chain_exhaustion_increments_recovery_cycle_count() -> None:
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, policy_bundle=_minimal_policy_bundle())
    )
    state = _make_state(["claude"])

    assert state.recovery_cycle_count == 0

    state = _fail_repeatedly(controller, state, "claude", _CONSECUTIVE_UNPRODUCTIVE_LIMIT)

    assert state.recovery_cycle_count == 1


def test_single_agent_chain_exhaustion_retains_no_fallover_records() -> None:
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, policy_bundle=_minimal_policy_bundle())
    )
    state = _make_state(["claude"])

    state = _fail_repeatedly(controller, state, "claude", _CONSECUTIVE_UNPRODUCTIVE_LIMIT)

    assert state.phase == "failed_terminal"
    assert state.recovery_cycle_count == 1
    assert state.fallover_history == ()
