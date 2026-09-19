"""Tests for in-session retry escalation in RecoveryController.

Verifies the in-session retry escalation requirements:
  - Default limit of 3 consecutive qualifying retries allows three reinstantiations,
    with cooldown triggered on the 4th consecutive qualifying retry.
  - Configured retry limit replaces the default limit of 3.
  - When an agent is placed on cooldown by escalation, the workflow advances
    to the next eligible agent that is not in cooldown.
  - If no eligible agent is available, the controller enters the wait state.
  - Successful completion of the retried work resets the agent's consecutive failure count.
  - Failures from one agent do not increase another agent's count.
  - Failures that already place an agent in cooldown are not escalated a second time.
  - Both technical categories (e.g. ARTIFACT_VALIDATION) and AGENT categories are
    counted through the common funnel.
  - Wiring from UnifiedConfig/GeneralConfig to RecoveryControllerOptions in
    ralph.pipeline.run_loop.build_recovery_controller preserves the configured limit.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.timeout_clock import FakeClock
from ralph.config.general_config import GeneralConfig
from ralph.config.models import UnifiedConfig
from ralph.pipeline.run_loop import build_recovery_controller
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_budget_registry import AgentBudgetRegistry
from ralph.recovery.classified_failure import ClassifiedFailure
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEventBus
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.unavailability_reason import UnavailabilityReason

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


def _minimal_policy_bundle() -> PolicyBundle:
    return load_policy(Path(__file__).parents[2] / "ralph" / "policy" / "defaults")


def _two_agent_state(current_index: int = 0, retries: int = 0) -> PipelineState:
    chain_state = AgentChainState(
        agents=["claude", "opencode"],
        current_index=current_index,
        retries=retries,
    )
    return PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    ).copy_with(last_connectivity_state="online")


def _single_agent_state(current_index: int = 0, retries: int = 0) -> PipelineState:
    chain_state = AgentChainState(
        agents=["claude"],
        current_index=current_index,
        retries=retries,
    )
    return PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    ).copy_with(last_connectivity_state="online")


def _controller(
    *,
    limit: int = 3,
    clock: FakeClock | None = None,
) -> RecoveryController:
    registry = AgentBudgetRegistry().set_budget("development", "claude", 10)
    registry = registry.set_budget("development", "opencode", 10)
    return RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=50,
            clock=clock or FakeClock(start=0.0),
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
            budget_registry=registry,
            in_session_retry_escalation_limit=limit,
        ),
    )


def _qualifying_failure(
    category: FailureCategory = FailureCategory.AGENT,
    *,
    is_unavailable: bool = False,
    agent: str = "claude",
) -> ClassifiedFailure:
    return ClassifiedFailure(
        category=category,
        reason=f"qualifying {category.value} failure",
        attributed_agent=agent,
        attributed_phase="development",
        counts_against_budget=(category == FailureCategory.AGENT),
        original_exception=RuntimeError("test error"),
        raw_message="test error",
        is_unavailable=is_unavailable,
    )


def test_options_reject_non_positive_limit() -> None:
    """A recovery controller cannot silently disable escalation."""
    try:
        RecoveryControllerOptions(in_session_retry_escalation_limit=0)
    except ValueError as exc:
        assert "in_session_retry_escalation_limit" in str(exc)
    else:
        raise AssertionError("a zero escalation limit must be rejected")


def test_default_limit_three_retries_then_cooldown_on_fourth() -> None:
    """Three consecutive qualifying retries keep same agent; fourth triggers cooldown and advances."""
    controller = _controller(limit=3)
    state = _two_agent_state(current_index=0, retries=0)
    failure = _qualifying_failure()

    # Retries 1, 2, 3: same agent re-instantiated (retries increments)
    for expected_retries in (1, 2, 3):
        ctx = FailureContext(
            phase="development",
            agent="claude",
            retry_in_session=True,
            classified_failure=failure,
        )
        new_state, _effects, _evt = controller.handle(state, "test error", ctx)
        chain = new_state.chain_for_phase("development")
        assert chain is not None
        assert chain.current_index == 0, f"agent should stay 0 on retry {expected_retries}"
        assert chain.retries == expected_retries
        assert new_state.is_waiting_state is False
        state = new_state

    # 4th failure: triggers escalation -> claude placed in cooldown -> advances to opencode
    ctx = FailureContext(
        phase="development",
        agent="claude",
        retry_in_session=True,
        classified_failure=failure,
    )
    new_state, _effects, _evt = controller.handle(state, "test error", ctx)
    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1, "4th consecutive retry must advance to next eligible agent"
    assert chain.retries == 0
    assert not controller.unavailability_store.is_available("development", "claude")


def test_configurable_limit_overrides_default() -> None:
    """Configured limit of 1 allows 1 retry, escalates on the 2nd."""
    controller = _controller(limit=1)
    state = _two_agent_state(current_index=0, retries=0)
    failure = _qualifying_failure()

    # Retry 1: allowed
    ctx = FailureContext(
        phase="development",
        agent="claude",
        retry_in_session=True,
        classified_failure=failure,
    )
    new_state, _effects, _evt = controller.handle(state, "test error", ctx)
    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 0
    assert chain.retries == 1

    # Retry 2: escalates
    new_state, _effects, _evt = controller.handle(new_state, "test error", ctx)
    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1
    assert not controller.unavailability_store.is_available("development", "claude")


def test_all_agents_unavailable_enters_wait_state() -> None:
    """When the sole agent escalates and no other agent is available, enters waiting state."""
    controller = _controller(limit=2)
    state = _single_agent_state(current_index=0, retries=0)
    failure = _qualifying_failure()

    ctx = FailureContext(
        phase="development",
        agent="claude",
        retry_in_session=True,
        classified_failure=failure,
    )
    # Retries 1 and 2
    state, _, _ = controller.handle(state, "test error", ctx)
    state, _, _ = controller.handle(state, "test error", ctx)
    chain = state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 0
    assert chain.retries == 2

    # 3rd retry: escalates to cooldown, all agents (only claude) unavailable -> wait state
    new_state, effects, _evt = controller.handle(state, "test error", ctx)
    assert new_state.is_waiting_state is True
    assert new_state.last_retry_delay_ms > 0
    assert effects == []


def test_successful_completion_resets_counter() -> None:
    """reset_backoff clears the consecutive in-session retry count."""
    controller = _controller(limit=2)
    state = _two_agent_state(current_index=0, retries=0)
    failure = _qualifying_failure()

    ctx = FailureContext(
        phase="development",
        agent="claude",
        retry_in_session=True,
        classified_failure=failure,
    )
    # Retry 1 and 2 (at limit)
    state, _, _ = controller.handle(state, "test error", ctx)
    state, _, _ = controller.handle(state, "test error", ctx)
    # Agent succeeds: reset_backoff called.
    controller.reset_backoff("development", "claude")

    # Next failure is retry 1 again, not an escalation.
    new_state, _effects, _evt = controller.handle(state, "test error", ctx)
    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 0, "Counter was reset, so claude is retried again"


def test_independent_agent_tracking() -> None:
    """Failures from one agent do not increase another agent's count."""
    controller = _controller(limit=2)
    state = _two_agent_state(current_index=0, retries=0)

    claude_failure = _qualifying_failure(agent="claude")
    opencode_failure = _qualifying_failure(agent="opencode")

    # Claude fails once
    ctx_claude = FailureContext(
        phase="development",
        agent="claude",
        retry_in_session=True,
        classified_failure=claude_failure,
    )
    state_after_claude, _, _ = controller.handle(state, "test error", ctx_claude)
    claude_chain = state_after_claude.chain_for_phase("development")
    assert claude_chain is not None
    assert claude_chain.current_index == 0
    assert claude_chain.retries == 1

    # Opencode fails while claude is on cooldown, so opencode remains the selected agent.
    controller.unavailability_store.mark_unavailable("development", "claude")
    state_opencode = _two_agent_state(current_index=1, retries=0)
    ctx_opencode = FailureContext(
        phase="development",
        agent="opencode",
        retry_in_session=True,
        classified_failure=opencode_failure,
    )
    state_after_opencode, _, _ = controller.handle(state_opencode, "test error", ctx_opencode)
    opencode_chain = state_after_opencode.chain_for_phase("development")
    assert opencode_chain is not None
    assert opencode_chain.current_index == 1
    assert opencode_chain.retries == 1


def test_failures_already_in_cooldown_do_not_escalate() -> None:
    """Failures with is_unavailable=True trigger cooldown directly without double-escalation."""
    controller = _controller(limit=3)
    state = _two_agent_state(current_index=0, retries=0)
    unavail_failure = ClassifiedFailure(
        category=FailureCategory.AGENT,
        reason="credits exhausted",
        attributed_agent="claude",
        attributed_phase="development",
        counts_against_budget=True,
        original_exception=RuntimeError("credits error"),
        raw_message="credits error",
        is_unavailable=True,
        unavailability_reason=UnavailabilityReason.OUT_OF_CREDITS,
    )
    ctx = FailureContext(
        phase="development",
        agent="claude",
        retry_in_session=True,
        classified_failure=unavail_failure,
    )
    new_state, _effects, _evt = controller.handle(state, "credits error", ctx)
    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1
    # The next eligible agent is selected without an additional cooldown.
    assert controller.unavailability_store.is_available("development", "opencode")


def test_technical_category_failure_escalates() -> None:
    """Technical categories (e.g. ARTIFACT_VALIDATION) escalate through the common funnel."""
    controller = _controller(limit=2)
    state = _two_agent_state(current_index=0, retries=0)
    artifact_failure = _qualifying_failure(category=FailureCategory.ARTIFACT_VALIDATION)

    ctx = FailureContext(
        phase="development",
        agent="claude",
        retry_in_session=True,
        classified_failure=artifact_failure,
    )
    # Retries 1 and 2
    state, _, _ = controller.handle(state, "artifact error", ctx)
    chain = state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 0

    state, _, _ = controller.handle(state, "artifact error", ctx)
    chain = state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 0

    # 3rd retry escalates to next agent
    new_state, _, _ = controller.handle(state, "artifact error", ctx)
    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1, "Technical failure must escalate to next agent"
    assert not controller.unavailability_store.is_available("development", "claude")


def test_run_loop_wiring_preserves_configured_limit() -> None:
    """build_recovery_controller wires GeneralConfig.in_session_retry_escalation_limit to the controller."""
    policy_bundle = _minimal_policy_bundle()
    state = PipelineState(phase="development")
    config = UnifiedConfig(general=GeneralConfig(in_session_retry_escalation_limit=1))

    ctrl, _cap = build_recovery_controller(state, policy_bundle, config)
    state = _two_agent_state()
    failure = _qualifying_failure()
    ctx = FailureContext(
        phase="development",
        agent="claude",
        retry_in_session=True,
        classified_failure=failure,
    )

    retried_state, _, _ = ctrl.handle(state, "test error", ctx)
    escalated_state, _, _ = ctrl.handle(retried_state, "test error", ctx)
    chain = escalated_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1
