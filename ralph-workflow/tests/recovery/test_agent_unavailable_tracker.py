"""Black-box tests for RecoveryController unavailable-agent timeout tracking."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._errors import AgentInvocationError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.run_loop import _apply_connectivity_check
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.classifier import FailureCategory, FailureClassifier
from ralph.recovery.connectivity import ConnectivityState
from ralph.recovery.controller import (
    FailureContext,
    RecoveryController,
    RecoveryControllerOptions,
    compute_backoff_ms,
)
from ralph.recovery.events import FailureEvent, FailureEventBus, FalloverEvent
from ralph.recovery.testing import FakeConnectivityMonitor
from ralph.recovery.unavailability_reason import (
    ReasonBackoffPolicy,
    UnavailabilityReason,
)


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


def _simulate_successful_run(controller: RecoveryController, phase: str, agent: str) -> None:
    """Mirror the production success path: runner resets backoff after a successful invocation."""
    controller.reset_backoff(phase, agent)


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
    starting_timeout_ms = 1_000
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            budget_registry=registry,
            clock=clock,
            event_bus=bus,
            unavailable_timeouts={"development:claude": starting_timeout_ms},
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

    snap = controller.snapshot()
    assert snap["backoff_attempts"]["development:claude"] == 1
    claude_timeout = snap["unavailable_timeouts"]["development:claude"]
    assert claude_timeout > starting_timeout_ms
    assert claude_timeout == 5_000

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


def test_all_agents_unavailable_waits_for_cooldown() -> None:
    """When every agent in the chain is unavailable, the session waits rather than failing."""
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            unavailable_timeouts={
                "development:claude": 60_000,
                "development:opencode": 60_000,
            },
        )
    )
    state = _make_state(["claude", "opencode"]).copy_with(
        last_connectivity_state="online"
    )

    new_state, effects, _ = controller.handle(
        state,
        AgentInvocationError("claude", 1, "agent produced no output for 60s"),
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.phase == "development"
    assert new_state.last_retry_delay_ms > 0
    assert effects == []
    assert new_state.last_error is not None
    assert "all agents unavailable" in new_state.last_error.lower()
    assert "waiting for cooldown expiry" in new_state.last_error.lower()

    snap = controller.snapshot()
    assert snap["backoff_attempts"]["development:claude"] == 1

    # Once at least one cooldown expires, the same phase can make progress.
    # Because claude fails again immediately, it is marked unavailable again
    # and the controller falls over to opencode rather than terminating.
    clock.advance(60)
    retried_state, retried_effects, _ = controller.handle(
        new_state,
        AgentInvocationError("claude", 1, "agent produced no output for 60s"),
        FailureContext(phase="development", agent="claude"),
    )
    assert retried_state.phase == "development"
    assert len(retried_effects) == 0
    assert retried_state.chain_for_phase("development").current_index == 1

    snap2 = controller.snapshot()
    assert snap2["backoff_attempts"]["development:claude"] == 2
    assert snap2["unavailable_timeouts"]["development:claude"] > 60_000


def test_all_agents_unavailable_waits_and_resumes_after_earliest_cooldown() -> None:
    """When every agent is unavailable, wait for the earliest cooldown then resume."""
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            unavailable_timeouts={
                "development:claude": 5_000,
                "development:opencode": 10_000,
            },
        )
    )
    state = _make_state(["claude", "opencode"]).copy_with(
        last_connectivity_state="online"
    )

    # Every agent is unavailable: the session waits for the earliest cooldown.
    waiting_state, effects, _ = controller.handle(
        state,
        AgentInvocationError("claude", 1, "agent produced no output for 60s"),
        FailureContext(phase="development", agent="claude"),
    )
    assert waiting_state.phase == "development"
    assert waiting_state.last_retry_delay_ms == 5_000
    assert effects == []
    assert waiting_state.last_error is not None
    assert "all agents unavailable" in waiting_state.last_error.lower()
    assert "waiting for cooldown expiry" in waiting_state.last_error.lower()
    snap = controller.snapshot()
    assert snap["backoff_attempts"]["development:claude"] == 1

    # Advance to the earliest cooldown expiry; claude can be reconsidered.
    clock.advance(5)

    # Claude fails again and is re-marked unavailable; opencode is still
    # cooling down, so the controller waits again rather than crashing.
    waiting_state2, effects2, _ = controller.handle(
        waiting_state,
        AgentInvocationError("claude", 1, "agent produced no output for 60s"),
        FailureContext(phase="development", agent="claude"),
    )
    assert waiting_state2.phase == "development"
    assert waiting_state2.last_retry_delay_ms == 5_000
    assert effects2 == []

    # Advance until opencode's cooldown also expires.
    clock.advance(5)

    # Claude fails again; opencode is now available, so the controller falls
    # over and the phase makes progress instead of terminating.
    resumed_state, resumed_effects, _ = controller.handle(
        waiting_state2,
        AgentInvocationError("claude", 1, "agent produced no output for 60s"),
        FailureContext(phase="development", agent="claude"),
    )
    assert resumed_state.phase == "development"
    assert resumed_state.chain_for_phase("development").current_index == 1
    assert resumed_effects == []


def test_unavailable_agent_fallover_a_to_b_to_a_with_exponential_backoff() -> None:
    """Agent A fails, fall over to B, then retry A after cooldown and grow its backoff."""
    clock = FakeClock(start=0.0)
    registry = _make_registry_with_budget("development", "claude", max_retries=2)
    registry = _make_registry_with_budget("development", "opencode", max_retries=2)
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
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _make_state(["claude", "opencode"]).copy_with(
        last_connectivity_state="online"
    )

    # A fails -> fall over to B.
    state_b, _, _ = controller.handle(
        state,
        AgentInvocationError("claude", 1, "agent produced no output for 60s"),
        FailureContext(phase="development", agent="claude"),
    )
    assert state_b.chain_for_phase("development").current_index == 1
    assert fallovers[-1].from_agent == "claude"
    assert fallovers[-1].to_agent == "opencode"
    snap1 = controller.snapshot()
    assert snap1["unavailable_timeouts"]["development:claude"] == 5_000
    assert snap1["backoff_attempts"]["development:claude"] == 1

    # Advance past A's cooldown so A can be reconsidered.
    clock.advance(5)

    # B fails -> fall back to A (A to B to A complete).
    state_a, _, _ = controller.handle(
        state_b,
        AgentInvocationError("opencode", 1, "agent produced no output for 60s"),
        FailureContext(phase="development", agent="opencode"),
    )
    assert state_a.chain_for_phase("development").current_index == 0
    assert fallovers[-1].from_agent == "opencode"
    assert fallovers[-1].to_agent == "claude"

    # Advance past B's cooldown so B can be reconsidered when A fails again.
    clock.advance(5)

    # A fails again -> fall over to B, and A's backoff grows from 5s to 10s.
    state_b2, _, _ = controller.handle(
        state_a,
        AgentInvocationError("claude", 1, "agent produced no output for 60s"),
        FailureContext(phase="development", agent="claude"),
    )
    assert state_b2.chain_for_phase("development").current_index == 1
    assert fallovers[-1].from_agent == "claude"
    assert fallovers[-1].to_agent == "opencode"
    snap2 = controller.snapshot()
    # Backoff duration grew from 5s to 10s (unavailable_timeouts stores absolute
    # expiration timestamps, so compute the remaining cooldown).
    current_time_ms = int(clock.monotonic() * 1000)
    assert snap2["unavailable_timeouts"]["development:claude"] - current_time_ms == 10_000
    assert snap2["backoff_attempts"]["development:claude"] == 2


def test_no_progress_quiet_reason_falls_over_a_to_b_to_a_with_growing_backoff() -> None:
    """Agent A fails due to NO_PROGRESS_QUIET, fall over to B with growing backoff."""
    clock = FakeClock(start=0.0)
    registry = _make_registry_with_budget("development", "claude", max_retries=2)
    registry = _make_registry_with_budget("development", "opencode", max_retries=2)
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
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _make_state(["claude", "opencode"]).copy_with(
        last_connectivity_state="online"
    )

    opts = InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_PROGRESS_QUIET,
        diagnostic={"cumulative": 0.0},
    )
    exc_a = AgentInactivityTimeoutError("claude", 1, opts=opts)
    exc_b = AgentInactivityTimeoutError("opencode", 1, opts=opts)

    # Cycle 1: A fails -> fall over to B.
    state_b, _, _ = controller.handle(
        state,
        exc_a,
        FailureContext(phase="development", agent="claude"),
    )
    assert state_b.chain_for_phase("development").current_index == 1
    assert fallovers[-1].from_agent == "claude"
    assert fallovers[-1].to_agent == "opencode"
    assert fallovers[-1].watchdog_reason == "no_progress_quiet"
    snap1 = controller.snapshot()
    assert snap1["unavailable_timeouts"]["development:claude"] == 5_000
    assert snap1["backoff_attempts"]["development:claude"] == 1

    # Advance past A's cooldown so A can be reconsidered.
    clock.advance(5)

    # Cycle 2: B fails -> fall back to A.
    state_a, _, _ = controller.handle(
        state_b,
        exc_b,
        FailureContext(phase="development", agent="opencode"),
    )
    assert state_a.chain_for_phase("development").current_index == 0
    assert fallovers[-1].from_agent == "opencode"
    assert fallovers[-1].to_agent == "claude"
    assert fallovers[-1].watchdog_reason == "no_progress_quiet"

    # Advance past B's cooldown so B can be reconsidered when A fails again.
    clock.advance(5)

    # Cycle 3: A fails again -> fall over to B, and A's backoff grows to 10s.
    state_b2, _, _ = controller.handle(
        state_a,
        exc_a,
        FailureContext(phase="development", agent="claude"),
    )
    assert state_b2.chain_for_phase("development").current_index == 1
    assert fallovers[-1].from_agent == "claude"
    assert fallovers[-1].to_agent == "opencode"
    assert fallovers[-1].watchdog_reason == "no_progress_quiet"

    snap2 = controller.snapshot()
    current_time_ms = int(clock.monotonic() * 1000)
    assert snap2["unavailable_timeouts"]["development:claude"] - current_time_ms == 10_000
    assert snap2["backoff_attempts"]["development:claude"] == 2

    # Advance past A's cooldown so A can be reconsidered when B fails again.
    clock.advance(10)

    # Cycle 4: B fails -> fall back to A, and A's backoff grows to 20s.
    state_a2, _, _ = controller.handle(
        state_b2,
        exc_b,
        FailureContext(phase="development", agent="opencode"),
    )
    assert state_a2.chain_for_phase("development").current_index == 0
    assert fallovers[-1].from_agent == "opencode"
    assert fallovers[-1].to_agent == "claude"
    assert fallovers[-1].watchdog_reason == "no_progress_quiet"

    # Advance past A's cooldown so A can be reconsidered.
    clock.advance(20)

    # Cycle 5: A fails again -> fall over to B, and A's backoff reaches cap.
    state_b3, _, _ = controller.handle(
        state_a2,
        exc_a,
        FailureContext(phase="development", agent="claude"),
    )
    assert state_b3.chain_for_phase("development").current_index == 1
    assert fallovers[-1].from_agent == "claude"
    assert fallovers[-1].to_agent == "opencode"
    assert fallovers[-1].watchdog_reason == "no_progress_quiet"

    snap3 = controller.snapshot()
    current_time_ms3 = int(clock.monotonic() * 1000)
    assert snap3["unavailable_timeouts"]["development:claude"] - current_time_ms3 == 20_000
    assert snap3["backoff_attempts"]["development:claude"] == 3


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
    assert snap1["backoff_attempts"]["development:claude"] == 1

    clock.advance(4.9)
    controller.handle(
        _fresh_state(),
        AgentInvocationError("claude", 1, msg),
        FailureContext(phase="development", agent="claude"),
    )
    snap2 = controller.snapshot()
    assert snap2["unavailable_timeouts"]["development:claude"] == 14_900
    assert snap2["backoff_attempts"]["development:claude"] == 2

    clock.advance(9.9)
    controller.handle(
        _fresh_state(),
        AgentInvocationError("claude", 1, msg),
        FailureContext(phase="development", agent="claude"),
    )
    snap3 = controller.snapshot()
    assert snap3["unavailable_timeouts"]["development:claude"] == 34_800
    assert snap3["backoff_attempts"]["development:claude"] == 3

    clock.advance(20.1)
    # Simulate the production success path: the runner resets backoff after
    # a successful agent invocation, clearing the attempt counter.
    _simulate_successful_run(controller, "development", "claude")
    snap_after_reset = controller.snapshot()
    assert "development:claude" not in snap_after_reset["backoff_attempts"]

    controller.handle(
        _fresh_state(),
        AgentInvocationError("claude", 1, msg),
        FailureContext(phase="development", agent="claude"),
    )
    snap4 = controller.snapshot()
    assert snap4["unavailable_timeouts"]["development:claude"] == 39_900
    assert snap4["backoff_attempts"]["development:claude"] == 1

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


# Transient credit-exhaustion / quota / rate-limit detection patterns.
# These can self-heal after a cooldown and should flag the agent unavailable.
# Each pattern must flag the agent as unavailable only when connectivity is online.
_NEW_SUBSCRIPTION_LIMIT_PATTERNS = [
    # OpenAI
    "429 - You exceeded your current quota, please check your plan and billing details",
    "insufficient_quota: You exceeded your current quota",
    "rate limit reached for requests",
    "billing hard limit reached",
    "monthly spend limit reached",
    "you've run out of credits",
    # Anthropic / Claude
    "rate_limit_error: Number of request tokens has exceeded your per-minute rate limit",
    "rate_limit_error: Number of output tokens has exceeded your per-minute rate limit",
    "billing_error: out of credits or spend limit reached",
    "out of credits",
    "spend limit reached",
    "rate limit reached",
    # Google / Gemini
    "429 Resource Exhausted: quota exceeded",
    "resource_exhausted",
    "quota limit has been exceeded",
    # Cohere / general
    "rate limit exceeded",
    "too many requests",
    # Cross-provider generic
    "credits exhausted",
    "no credits remaining",
    "insufficient credits",
    "insufficient balance",
    "usage limit exceeded",
    "request limit exceeded",
    "token limit exceeded",
    "plan limit reached",
    "billing threshold reached",
    "free tier limit exceeded",
    "daily limit reached",
    "hourly limit reached",
    "per-minute limit exceeded",
]

# Permanent account/billing/configuration failures that will not self-heal.
# These must terminate through the normal failure path, not enter an
# indefinite unavailable cooldown.
_PERMANENT_ACCOUNT_PATTERNS = [
    "organization has no valid billing information",
    "your account is not active",
    "payment required",
    "account suspended",
    "subscription expired",
    "trial expired",
]


def test_classifier_new_subscription_patterns_online() -> None:
    """New credit/quota/rate-limit patterns set is_unavailable=True when online."""
    classifier = FailureClassifier()
    for message in _NEW_SUBSCRIPTION_LIMIT_PATTERNS:
        failure = classifier.classify(
            AgentInvocationError("claude", 1, message),
            phase="development",
            agent="claude",
            connectivity_state="online",
        )
        assert failure.category == FailureCategory.AGENT, message
        assert failure.is_unavailable is True, message


def test_classifier_new_subscription_patterns_not_unavailable_when_offline() -> None:
    """Credit/quota patterns do not set is_unavailable when connectivity is not online."""
    classifier = FailureClassifier()
    for connectivity in ("unknown", "offline"):
        for message in _NEW_SUBSCRIPTION_LIMIT_PATTERNS:
            failure = classifier.classify(
                AgentInvocationError("claude", 1, message),
                phase="development",
                agent="claude",
                connectivity_state=connectivity,
            )
            assert failure.category == FailureCategory.AGENT, message
            assert failure.is_unavailable is False, (connectivity, message)


def test_credit_exhaustion_failure_sets_timeout() -> None:
    """Credit-exhaustion messages store the same per-phase:agent unavailable timeout."""
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
        AgentInvocationError("claude", 1, "429 - You exceeded your current quota"),
        FailureContext(phase="development", agent="claude"),
    )

    snap = controller.snapshot()
    assert snap["unavailable_timeouts"]["development:claude"] == 5_000


def test_credit_exhaustion_agent_skipped_in_recovery_cycle() -> None:
    """A credit-exhaustion failure causes fallover to the next agent in the chain."""
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
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _make_state(["claude", "opencode"]).copy_with(
        last_connectivity_state="online"
    )

    new_state, _, evt = controller.handle(
        state,
        AgentInvocationError("claude", 1, "rate_limit_error: quota exceeded"),
        FailureContext(phase="development", agent="claude"),
    )

    assert evt.category == "agent"
    assert new_state.chain_for_phase("development").current_index == 1
    assert len(fallovers) == 1
    assert fallovers[0].from_agent == "claude"
    assert fallovers[0].to_agent == "opencode"


def test_all_agents_unavailable_credit_exhaustion_waits_for_cooldown() -> None:
    """Credit-exhaustion across the whole chain waits rather than crashing."""
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            unavailable_timeouts={
                "development:claude": 60_000,
                "development:opencode": 60_000,
            },
        )
    )
    state = _make_state(["claude", "opencode"]).copy_with(
        last_connectivity_state="online"
    )

    new_state, effects, _ = controller.handle(
        state,
        AgentInvocationError("claude", 1, "insufficient credits"),
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.phase == "development"
    assert new_state.last_retry_delay_ms > 0
    assert effects == []
    assert new_state.last_error is not None
    assert "all agents unavailable" in new_state.last_error.lower()
    assert "waiting for cooldown expiry" in new_state.last_error.lower()

    clock.advance(60)
    retried_state, retried_effects, _ = controller.handle(
        new_state,
        AgentInvocationError("claude", 1, "insufficient credits"),
        FailureContext(phase="development", agent="claude"),
    )
    assert retried_state.phase == "development"
    assert retried_state.chain_for_phase("development").current_index == 1
    assert retried_effects == []


def test_permanent_account_failures_classify_as_user_config() -> None:
    """Permanent billing/account failures are user-config, not agent unavailable."""
    classifier = FailureClassifier()
    for message in _PERMANENT_ACCOUNT_PATTERNS:
        failure = classifier.classify(
            AgentInvocationError("claude", 1, message),
            phase="development",
            agent="claude",
            connectivity_state="online",
        )
        assert failure.category == FailureCategory.USER_CONFIG, message
        assert failure.is_unavailable is False, message


def test_permanent_account_failure_terminates_instead_of_waiting() -> None:
    """Permanent billing failures advance to the failed route instead of waiting."""
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _make_state(["claude"]).copy_with(last_connectivity_state="online")

    new_state, effects, evt = controller.handle(
        state,
        AgentInvocationError("claude", 1, "organization has no valid billing information"),
        FailureContext(phase="development", agent="claude"),
    )

    assert evt.category == "user_config"
    assert new_state.last_connectivity_state == "online"
    # The phase should fail rather than entering an indefinite unavailable wait.
    assert new_state.phase != "development" or effects
    assert new_state.last_error is not None
    assert "all agents unavailable" not in (new_state.last_error or "").lower()


def test_online_connectivity_propagates_through_run_loop_wiring() -> None:
    """An ONLINE monitor writes 'online' into state so subscription limits trigger unavailable."""
    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)
    state = _make_state(["claude", "opencode"])
    # Default production state starts with unknown connectivity.
    assert state.last_connectivity_state == "unknown"

    updated_state = _apply_connectivity_check(state, monitor)

    assert updated_state.last_connectivity_state == "online"

    # A quota failure on the now-online state is classified as unavailable and falls over.
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=FakeClock(start=0.0),
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    new_state, _, evt = controller.handle(
        updated_state,
        AgentInvocationError("claude", 1, "insufficient_quota: quota exceeded"),
        FailureContext(phase="development", agent="claude"),
    )
    assert evt.category == "agent"
    assert new_state.chain_for_phase("development").current_index == 1


def test_all_agents_unavailable_event_reports_real_cooldown_delay() -> None:
    """The returned FailureEvent reports the actual cooldown wait, not zero."""
    clock = FakeClock(start=0.0)
    collected: list[FailureEvent] = []
    bus = FailureEventBus()
    bus.subscribe(lambda evt: collected.append(evt) if isinstance(evt, FailureEvent) else None)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            event_bus=bus,
            unavailable_timeouts={
                "development:claude": 60_000,
                "development:opencode": 60_000,
            },
        )
    )
    state = _make_state(["claude", "opencode"]).copy_with(
        last_connectivity_state="online"
    )

    new_state, effects, evt = controller.handle(
        state,
        AgentInvocationError("claude", 1, "agent produced no output for 60s"),
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.last_retry_delay_ms > 0
    assert evt.retry_delay_ms == new_state.last_retry_delay_ms
    assert effects == []
    assert new_state.last_error is not None
    assert "all agents unavailable" in new_state.last_error.lower()
    # At least one published event carries the real cooldown delay.
    assert any(e.retry_delay_ms == new_state.last_retry_delay_ms for e in collected)



def test_out_of_credits_uses_30min_cap() -> None:
    """OUT_OF_CREDITS reason caps at 30 minutes."""
    clock = FakeClock(start=0.0)
    policy = {UnavailabilityReason.OUT_OF_CREDITS: ReasonBackoffPolicy(base=60_000, max=1_800_000)}
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            unavailability_backoff_policy=policy,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _make_state(["claude"]).copy_with(last_connectivity_state="online")

    for _ in range(10):
        controller.handle(
            state,
            AgentInvocationError("claude", 1, "out of credits"),
            FailureContext(phase="development", agent="claude"),
        )
        clock.advance(3000)

    snap = controller.snapshot()
    current_time_ms = int(clock.monotonic() * 1000)
    timeout = snap["unavailable_timeouts"]["development:claude"]
    remaining = timeout - current_time_ms
    assert remaining == 1_800_000


def test_no_output_at_start_uses_30s_cap() -> None:
    """NO_OUTPUT_AT_START reason caps at 30 seconds."""
    clock = FakeClock(start=0.0)
    policy = {UnavailabilityReason.NO_OUTPUT_AT_START: ReasonBackoffPolicy(base=5_000, max=30_000)}
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            unavailability_backoff_policy=policy,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _make_state(["claude"]).copy_with(last_connectivity_state="online")

    for _ in range(10):
        controller.handle(
            state,
            AgentInvocationError("claude", 1, "no output"),
            FailureContext(phase="development", agent="claude"),
        )
        clock.advance(3000)

    snap = controller.snapshot()
    current_time_ms = int(clock.monotonic() * 1000)
    timeout = snap["unavailable_timeouts"]["development:claude"]
    remaining = timeout - current_time_ms
    assert remaining == 30_000


def test_stale_child_quiet_uses_5min_cap() -> None:
    """STALE_CHILD_QUIET reason caps at 5 minutes."""
    clock = FakeClock(start=0.0)
    policy = {UnavailabilityReason.STALE_CHILD_QUIET: ReasonBackoffPolicy(base=15_000, max=300_000)}
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            unavailability_backoff_policy=policy,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _make_state(["claude"]).copy_with(last_connectivity_state="online")

    for _ in range(10):
        controller.handle(
            state,
            AgentInvocationError("claude", 1, "no output"),
            FailureContext(phase="development", agent="claude"),
        )
        clock.advance(3000)

    snap = controller.snapshot()
    current_time_ms = int(clock.monotonic() * 1000)
    timeout = snap["unavailable_timeouts"]["development:claude"]
    remaining = timeout - current_time_ms
    assert remaining == 300_000


def test_failure_event_carries_unavailability_reason() -> None:
    """FailureEvent carries the unavailability_reason string."""
    collected: list[FailureEvent] = []
    bus = FailureEventBus()
    bus.subscribe(lambda evt: collected.append(evt) if isinstance(evt, FailureEvent) else None)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=FakeClock(start=0.0),
            event_bus=bus,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _make_state(["claude"]).copy_with(last_connectivity_state="online")

    controller.handle(
        state,
        AgentInvocationError("claude", 1, "out of credits"),
        FailureContext(phase="development", agent="claude"),
    )

    assert len(collected) >= 1
    evt = collected[0]
    assert evt.unavailability_reason == "out_of_credits"


def test_fallover_event_carries_unavailability_reason() -> None:
    """FalloverEvent carries the unavailability_reason string when available."""
    fallovers: list[FalloverEvent] = []
    bus = FailureEventBus()
    bus.subscribe(lambda evt: fallovers.append(evt) if isinstance(evt, FalloverEvent) else None)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            budget_registry=_make_registry_with_budget("development", "claude", max_retries=2),
            clock=FakeClock(start=0.0),
            event_bus=bus,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _make_state(["claude", "opencode"]).copy_with(last_connectivity_state="online")

    controller.handle(
        state,
        AgentInvocationError("claude", 1, "out of credits"),
        FailureContext(phase="development", agent="claude"),
    )

    assert len(fallovers) == 1
    assert fallovers[0].unavailability_reason == "out_of_credits"


def test_state_carries_last_unavailability_reason() -> None:
    """PipelineState carries last_unavailability_reason after handle()."""
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=FakeClock(start=0.0),
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _make_state(["claude"]).copy_with(last_connectivity_state="online")

    new_state, _, _ = controller.handle(
        state,
        AgentInvocationError("claude", 1, "out of credits"),
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.last_unavailability_reason == "out_of_credits"


def test_legacy_unavailable_timeouts_seam_still_works() -> None:
    """Pre-seeding unavailable_timeouts dict still routes 'all agents unavailable' branch."""
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=FakeClock(start=0.0),
            unavailable_timeouts={"development:claude": 60_000},
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

    assert "all agents unavailable" in new_state.last_error.lower()
    assert new_state.last_retry_delay_ms > 0
