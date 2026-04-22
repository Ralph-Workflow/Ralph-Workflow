"""RecoveryController: single owner of failure classification, budget, and fallover."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from loguru import logger

from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.classifier import ClassifiedFailure, FailureCategory, FailureClassifier
from ralph.recovery.cycle_cap import CycleCap
from ralph.recovery.events import FailureEvent, FailureEventBus, FalloverEvent

if TYPE_CHECKING:
    from ralph.pipeline.effects import Effect
    from ralph.pipeline.state import PipelineState


class RecoveryController:
    """Single conceptual owner of recovery logic.

    Handles classification, budget debiting, chain fallover, and cycle cap.
    Delegates nothing to the reducer's internal retry counter when active.
    """

    def __init__(
        self,
        *,
        cycle_cap: int = 200,
        classifier: FailureClassifier | None = None,
        event_bus: FailureEventBus | None = None,
        budget_registry: AgentBudgetRegistry | None = None,
    ) -> None:
        self._cap = CycleCap(cap=cycle_cap)
        self._classifier = classifier or FailureClassifier()
        self._bus = event_bus or FailureEventBus()
        self._registry = budget_registry or AgentBudgetRegistry()

    @property
    def event_bus(self) -> FailureEventBus:
        return self._bus

    @property
    def budget_registry(self) -> AgentBudgetRegistry:
        return self._registry

    def handle(
        self,
        state: PipelineState,
        raw_failure: BaseException | str,
        *,
        phase: str,
        agent: str | None,
    ) -> tuple[PipelineState, list[Effect], FailureEvent]:
        """Classify a failure and compute the recovery transition.

        Args:
            state: Current pipeline state.
            raw_failure: The raw exception or string error message.
            phase: Pipeline phase where the failure occurred.
            agent: Current agent name, if known.

        Returns:
            Tuple of (new_state, effects, failure_event).
        """
        from ralph.pipeline.effects import ExitFailureEffect  # noqa: PLC0415

        failure = self._classifier.classify(raw_failure, phase=phase, agent=agent)

        chain = state.chain_for_phase(phase)
        chain_capacity = 0
        if chain is not None:
            chain_capacity = max(0, len(chain.agents) - chain.current_index - 1)

        failure_evt = FailureEvent(
            timestamp=datetime.now(UTC),
            phase=phase,
            agent=agent,
            category=str(failure.category),
            reason=failure.reason,
            counted_against_budget=failure.counts_against_budget,
            chain_capacity_remaining=chain_capacity,
            recovery_cycle=state.recovery_cycle_count,
        )
        self._bus.publish(failure_evt)

        new_state = state.copy_with(last_failure_category=str(failure.category))

        if failure.category == FailureCategory.ENVIRONMENTAL:
            logger.info(
                "Environmental failure in phase={} (not counted against budget): {}",
                phase,
                failure.reason[:200],
            )
            return new_state, [], failure_evt

        if failure.category == FailureCategory.AMBIGUOUS:
            return new_state, [], failure_evt

        if failure.category == FailureCategory.USER_CONFIG:
            logger.error(
                "User/config failure reached runtime controller in phase={} (bug): {}",
                phase,
                failure.reason[:200],
            )
            return self._enter_phase_failed(
                new_state, failure.reason, failure.category
            ), [], failure_evt

        # AGENT category: debit budget and handle chain progression
        if agent is not None:
            self._registry = self._registry.debit(phase, agent, failure)

        new_state, effects = self._handle_agent_budget_exhaustion(
            new_state, failure, phase, agent
        )

        if self._cap.is_exceeded(new_state.recovery_cycle_count):
            exit_reason = self._cap.exit_reason(
                new_state.recovery_cycle_count,
                str(failure.category),
                failure.reason[:200],
            )
            logger.error("Recovery cycle cap exceeded: {}", exit_reason)
            return new_state, [ExitFailureEffect(reason=exit_reason)], failure_evt

        return new_state, effects, failure_evt

    def _handle_agent_budget_exhaustion(
        self,
        state: PipelineState,
        failure: ClassifiedFailure,
        phase: str,
        agent: str | None,
    ) -> tuple[PipelineState, list[Effect]]:
        """Handle agent failure with budget debit and chain progression."""
        from ralph.pipeline.state import AgentChainState, FalloverRecord  # noqa: PLC0415

        chain = state.chain_for_phase(phase)
        if chain is None:
            return state, []

        current_agent = agent or (
            chain.agents[chain.current_index]
            if chain.agents and chain.current_index < len(chain.agents)
            else None
        )
        max_retries = 3
        if current_agent is not None:
            budget_state = self._registry.get(phase, current_agent)
            if budget_state is not None:
                if not budget_state.exhausted:
                    new_chain = AgentChainState(
                        agents=chain.agents,
                        current_index=chain.current_index,
                        retries=chain.retries + 1,
                    )
                    return state.with_phase_chain(phase, new_chain), []
            elif chain.retries < max_retries:
                new_chain = AgentChainState(
                    agents=chain.agents,
                    current_index=chain.current_index,
                    retries=chain.retries + 1,
                )
                return state.with_phase_chain(phase, new_chain), []

        if chain.current_index + 1 < len(chain.agents):
            next_agent = chain.agents[chain.current_index + 1]
            from_agent = current_agent or f"agent[{chain.current_index}]"
            fallover_record = FalloverRecord(
                phase=phase,
                from_agent=from_agent,
                to_agent=next_agent,
                timestamp_iso=datetime.now(UTC).isoformat(),
            )
            fallover_evt = FalloverEvent.now(
                phase=phase,
                from_agent=from_agent,
                to_agent=next_agent,
                reason=failure.reason,
            )
            self._bus.publish(fallover_evt)

            new_chain = AgentChainState(
                agents=chain.agents,
                current_index=chain.current_index + 1,
                retries=0,
            )
            new_state = state.with_phase_chain(phase, new_chain).copy_with(
                fallover_history=(*state.fallover_history, fallover_record),
            )
            return new_state, []

        new_state = state.copy_with(
            recovery_cycle_count=state.recovery_cycle_count + 1,
        )
        failed_state = self._enter_phase_failed(
            new_state, failure.reason, failure.category
        )
        return failed_state, []

    def _enter_phase_failed(
        self,
        state: PipelineState,
        reason: str,
        category: object,
    ) -> PipelineState:
        from ralph.config.enums import PHASE_FAILED  # noqa: PLC0415

        return state.copy_with(
            phase=PHASE_FAILED,
            previous_phase=state.phase,
            last_error=reason,
            recovery_epoch=state.recovery_epoch + 1,
            last_failure_category=str(category),
        )
