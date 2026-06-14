"""Black-box tests for RecoveryController unavailable-agent timeout tracking."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._errors import AgentInvocationError
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.classifier import FailureCategory, FailureClassifier
from ralph.recovery.controller import (
    FailureContext,
    RecoveryController,
    RecoveryControllerOptions,
    compute_backoff_ms,
)
from ralph.recovery.events import FailureEventBus, FalloverEvent


def _minimal_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _make_state(agents: list[str]) -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=agents, current_index=0, retries=0)},
    )


def _make_registry_with_budget(phase: str, agent: str, max_retries: int = 1) -> AgentBudgetRegistry:
    return AgentBudgetRegistry().set_budget(phase, agent, max_retries)


def test_unavailable_failure_sets_timeout() -> None:
    """Marking an agent unavailable stores a per-phase:agent timeout."""
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _make_state(["claude"]).copy_with(last_connectivity_state="online")

    controller.handle(
        state,
        AgentInvocationError("claude", 1, "agent produced no output for 60s"),
        FailureContext(phase="development", agent="claude"),
    )

    snap = controller.snapshot()
    assert snap["unavailable_timeouts"]["development:claude"] == 5_000


def test_unavailable_timeout_is_per_phase_agent_pair() -> None:
    """Timeouts are isolated per phase:agent key."""
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    base_state = _make_state(["claude"]).copy_with(last_connectivity_state="online")

    other_state = PipelineState(
        phase="review",
        phase_chains={"review": AgentChainState(agents=["claude"], current_index=0, retries=0)},
    ).copy_with(last_connectivity_state="online")

    controller.handle(
        base_state,
        AgentInvocationError("claude", 1, "agent produced no output for 60s"),
        FailureContext(phase="development", agent="claude"),
    )
    controller.handle(
        other_state,
        AgentInvocationError("claude", 1, "agent produced no output for 60s"),
        FailureContext(phase="review", agent="claude"),
    )

    snap = controller.snapshot()
    assert snap["unavailable_timeouts"]["development:claude"] == 5_000
    assert snap["unavailable_timeouts"]["review:claude"] == 5_000


def test_unavailable_agent_skipped_in_recovery_cycle() -> None:
    """An unavailable agent is skipped; it is retried after its timeout expires."""
    clock = FakeClock(start=0.0)
    registry = _make_registry_with_budget("development", "claude", max_retries=2)
    fallovers: list[FalloverEvent] = []
    bus = FailureEventBus()
    bus.subscribe(
        lambda evt: fallovers.append(evt) if isinstance(evt, FalloverEvent) else None
    )
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            budget_registry=registry,
            clock=clock,
            event_bus=bus,
            unavailable_timeouts={"development:claude": 60_000},
        )
    )
    state = _make_state(["claude", "opencode"]).copy_with(
        last_connectivity_state="online"
    )

    new_state, _, evt = controller.handle(
        state,
        AgentInvocationError("claude", 1, "agent produced no output for 60s"),
        FailureContext(phase="development", agent="claude"),
    )

    assert evt.category == "agent"
    assert new_state.chain_for_phase("development").current_index == 1
    assert len(fallovers) == 1
    assert fallovers[0].from_agent == "claude"
    assert fallovers[0].to_agent == "opencode"

    # Cooldown for claude expires; a later failure on opencode routes back to
    # claude when opencode is also marked unavailable.
    clock.advance(60)
    retried_state, _, _ = controller.handle(
        new_state,
        AgentInvocationError("opencode", 1, "agent produced no output for 60s"),
        FailureContext(phase="development", agent="opencode"),
    )
    assert retried_state.chain_for_phase("development").current_index == 0
    assert any(
        f.from_agent == "opencode" and f.to_agent == "claude"
        for f in retried_state.fallover_history
    )


def test_all_agents_unavailable_triggers_phase_failure() -> None:
    """When every agent in the chain is unavailable, the phase fails."""
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            unavailable_timeouts={
                "development:claude": 60_000,
                "development:opencode": 60_000,
            },
        )
    )
    state = _make_state(["claude", "opencode"]).copy_with(
        last_connectivity_state="online"
    )

    new_state, _, _ = controller.handle(
        state,
        AgentInvocationError("claude", 1, "agent produced no output for 60s"),
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.phase == "failed_terminal"


def test_unavailable_skip_does_not_consume_budget() -> None:
    """Falling over from an unavailable agent must not debit its budget."""
    registry = _make_registry_with_budget("development", "claude", max_retries=2)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            budget_registry=registry,
            clock=FakeClock(start=0.0),
            unavailable_timeouts={"development:claude": 60_000},
        )
    )
    state = _make_state(["claude", "opencode"]).copy_with(
        last_connectivity_state="online"
    )

    controller.handle(
        state,
        AgentInvocationError("claude", 1, "agent produced no output for 60s"),
        FailureContext(phase="development", agent="claude"),
    )

    budget = controller.budget_registry.get("development", "claude")
    assert budget is not None
    assert budget.consumed == 0


def test_exponential_backoff_increases_across_cycles() -> None:
    """Consecutive unavailable failures grow the skip timeout exponentially."""
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    msg = "agent produced no output for 60s"

    def _fresh_state() -> PipelineState:
        return _make_state(["claude"]).copy_with(last_connectivity_state="online")

    controller.handle(
        _fresh_state(),
        AgentInvocationError("claude", 1, msg),
        FailureContext(phase="development", agent="claude"),
    )
    snap1 = controller.snapshot()
    assert snap1["unavailable_timeouts"]["development:claude"] == 5_000

    clock.advance(4.9)
    controller.handle(
        _fresh_state(),
        AgentInvocationError("claude", 1, msg),
        FailureContext(phase="development", agent="claude"),
    )
    snap2 = controller.snapshot()
    assert snap2["unavailable_timeouts"]["development:claude"] == 14_900

    clock.advance(9.9)
    controller.handle(
        _fresh_state(),
        AgentInvocationError("claude", 1, msg),
        FailureContext(phase="development", agent="claude"),
    )
    snap3 = controller.snapshot()
    assert snap3["unavailable_timeouts"]["development:claude"] == 34_800

    clock.advance(20.1)
    controller.reset_backoff("development", "claude")
    controller.handle(
        _fresh_state(),
        AgentInvocationError("claude", 1, msg),
        FailureContext(phase="development", agent="claude"),
    )
    snap4 = controller.snapshot()
    assert snap4["unavailable_timeouts"]["development:claude"] == 39_900

    assert compute_backoff_ms(5_000, 0, 300_000) == 5_000
    assert compute_backoff_ms(5_000, 1, 300_000) == 10_000
    assert compute_backoff_ms(5_000, 2, 300_000) == 20_000
    assert compute_backoff_ms(5_000, 10, 300_000) == 300_000


def test_classifier_flags_unavailable_only_when_online() -> None:
    """Unavailable flag is set only for online no-output failures."""
    classifier = FailureClassifier()
    message = "agent produced no output for 60s"

    for connectivity in ("unknown", "offline"):
        failure = classifier.classify(
            AgentInvocationError("claude", 1, message),
            phase="development",
            agent="claude",
            connectivity_state=connectivity,
        )
        assert failure.category == FailureCategory.AGENT
        assert failure.is_unavailable is False

    online_failure = classifier.classify(
        AgentInvocationError("claude", 1, message),
        phase="development",
        agent="claude",
        connectivity_state="online",
    )
    assert online_failure.category == FailureCategory.AGENT
    assert online_failure.is_unavailable is True

    network_failure = classifier.classify(
        AgentInvocationError("claude", 1, "Connection reset by peer"),
        phase="development",
        agent="claude",
        connectivity_state="online",
    )
    assert network_failure.category == FailureCategory.ENVIRONMENTAL
    assert network_failure.is_unavailable is False


def test_agent_inactivity_timeout_is_unavailable() -> None:
    """The real AgentInactivityTimeoutError subclass flags the agent unavailable."""
    failure = FailureClassifier().classify(
        AgentInactivityTimeoutError("claude", 30.0),
        phase="development",
        agent="claude",
        connectivity_state="online",
    )
    assert failure.category == FailureCategory.AGENT
    assert failure.is_unavailable is True


def test_post_tool_empty_response_stays_retryable_not_unavailable() -> None:
    """Post-tool registry-wedge failures keep their bounded retry path.

    A tool-result desync (empty response after prior tool activity) is a
    tool-registry issue, not an out-of-credits unavailability, and must not
    be routed into the unavailable cooldown path.
    """
    exc = AgentInvocationError(
        "claude",
        1,
        "stderr text",
        parsed_output=[
            '{"type":"tool_result"}',
            "Model returned an empty response with no tool calls",
        ],
    )
    failure = FailureClassifier().classify(
        exc,
        phase="development",
        agent="claude",
        connectivity_state="online",
    )
    assert failure.category == FailureCategory.AGENT
    assert failure.reset_tool_registry is True
    assert failure.is_unavailable is False
