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
    from ralph.pipeline.state import AgentChainState, PipelineState
    from ralph.policy.models import AgentChainConfig, PolicyBundle


def compute_backoff_ms(base_ms: int, attempt: int, max_ms: int = 30_000) -> int:
    """Compute exponential backoff delay with cap.

    Args:
        base_ms: Base delay in milliseconds.
        attempt: Current retry attempt (0-indexed).
        max_ms: Maximum delay cap in milliseconds.

    Returns:
        Delay in milliseconds, capped at max_ms.
    """
    exponent_factor: int = 2**attempt
    delay: int = base_ms * exponent_factor
    return min(delay, max_ms)


class RecoveryController:
    """Single conceptual owner of recovery logic.

    Handles classification, budget debiting, chain fallover, and cycle cap.
    Delegates nothing to the reducer's internal retry counter when active.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        cycle_cap: int = 200,
        classifier: FailureClassifier | None = None,
        event_bus: FailureEventBus | None = None,
        budget_registry: AgentBudgetRegistry | None = None,
        policy_bundle: PolicyBundle | None = None,
        backoff_attempts: dict[str, int] | None = None,
    ) -> None:
        self._cap = CycleCap(cap=cycle_cap)
        self._classifier = classifier or FailureClassifier()
        self._bus = event_bus or FailureEventBus()
        self._registry = budget_registry or AgentBudgetRegistry()
        self._policy_bundle = policy_bundle
        self._backoff_attempts: dict[str, int] = backoff_attempts or {}

    @property
    def event_bus(self) -> FailureEventBus:
        return self._bus

    @property
    def budget_registry(self) -> AgentBudgetRegistry:
        return self._registry

    def handle(  # noqa: PLR0913
        self,
        state: PipelineState,
        raw_failure: BaseException | str,
        *,
        phase: str,
        agent: str | None,
        retry_in_session: bool = False,
        classified_failure: ClassifiedFailure | None = None,
    ) -> tuple[PipelineState, list[Effect], FailureEvent]:
        """Classify a failure and compute the recovery transition.

        Args:
            state: Current pipeline state.
            raw_failure: The raw exception or string error message.
            phase: Pipeline phase where the failure occurred.
            agent: Current agent name, if known.
            retry_in_session: When True and state carries a captured session ID,
                the AGENT-category retry path sets session_preserve_retry_pending
                so the runner reuses the agent session rather than cold-starting.
            classified_failure: Optional pre-classified failure object for known
                phase-level failures. When supplied, recovery must honor it
                directly instead of re-classifying the string reason.

        Returns:
            Tuple of (new_state, effects, failure_event).
        """
        # Lazy import: ralph.pipeline.effects depends transitively on recovery types.
        from ralph.pipeline.effects import ExitFailureEffect  # noqa: PLC0415

        failure = classified_failure or self._classifier.classify(
            raw_failure, phase=phase, agent=agent
        )

        chain = state.chain_for_phase(phase)
        chain_capacity = 0
        retry_delay_ms = 0

        if chain is not None:
            chain_capacity = max(0, len(chain.agents) - chain.current_index - 1)

            # Compute retry delay from chain config
            if agent is not None and failure.counts_against_budget:
                retry_delay_ms = self._compute_retry_delay(phase, agent)

        failure_evt = FailureEvent(
            timestamp=datetime.now(UTC),
            phase=phase,
            agent=agent,
            category=str(failure.category),
            reason=failure.reason,
            counted_against_budget=failure.counts_against_budget,
            chain_capacity_remaining=chain_capacity,
            recovery_cycle=state.recovery_cycle_count,
            retry_delay_ms=retry_delay_ms,
        )
        self._bus.publish(failure_evt)

        # ALWAYS set last_failure_category and last_retry_delay_ms on state first
        new_state = state.copy_with(
            last_failure_category=str(failure.category),
            last_retry_delay_ms=retry_delay_ms,
        )

        if failure.category == FailureCategory.ENVIRONMENTAL:
            logger.info(
                "Environmental failure in phase={} (not counted against budget): {}",
                phase,
                failure.reason[:200],
            )
            # Environmental failures retry immediately without debiting budget or retries.
            return new_state.copy_with(last_error=failure.reason), [], failure_evt

        if failure.category in (
            FailureCategory.ARTIFACT_VALIDATION,
            FailureCategory.AMBIGUOUS,
        ):
            category_label = (
                "Artifact validation"
                if failure.category == FailureCategory.ARTIFACT_VALIDATION
                else "Ambiguous"
            )
            logger.info(
                "{} failure in phase={} (retry without budget debit): {}",
                category_label,
                phase,
                failure.reason[:200],
            )
            # Non-budget retries track retry count and may preserve session.
            new_state = new_state.copy_with(last_error=failure.reason)
            new_state = self._increment_chain_retries(new_state, phase)
            if retry_in_session and new_state.last_agent_session_id:
                new_state = new_state.copy_with(session_preserve_retry_pending=True)
            return new_state, [], failure_evt

        if failure.category == FailureCategory.USER_CONFIG:
            logger.error(
                "User/config failure reached runtime controller in phase={} (bug): {}",
                phase,
                failure.reason[:200],
            )
            return (
                self._enter_phase_failed(new_state, failure.reason, failure.category),
                [],
                failure_evt,
            )

        # AGENT category: debit budget and handle chain progression
        if failure.reset_session:
            logger.warning(
                "Stale session detected in phase={} (session id invalid): {}",
                phase,
                failure.reason[:200],
            )
            new_state = new_state.copy_with(
                last_agent_session_id=None,
                session_preserve_retry_pending=False,
            )
            self._write_session_reset_hint(phase, failure)

        if agent is not None:
            self._registry = self._registry.debit(phase, agent, failure)
            # Track backoff attempt
            if failure.counts_against_budget:
                key = f"{phase}:{agent}"
                self._backoff_attempts[key] = self._backoff_attempts.get(key, 0) + 1

        new_state, effects = self._handle_agent_budget_exhaustion(
            new_state, failure, phase, agent, retry_in_session=retry_in_session
        )

        if self._cap.is_exceeded(new_state.recovery_cycle_count):
            exit_reason = self._cap.exit_reason(
                new_state.recovery_cycle_count,
                str(failure.category),
                failure.reason[:200],
            )
            logger.error("Recovery cycle cap exceeded: {}", exit_reason)
            # Cycle exceeded: no retry delay
            return (
                new_state.copy_with(last_retry_delay_ms=0),
                [ExitFailureEffect(reason=exit_reason)],
                failure_evt,
            )

        return new_state, effects, failure_evt

    def _increment_chain_retries(self, state: PipelineState, phase: str) -> PipelineState:
        """Increment chain.retries for the given phase without debiting the budget."""
        chain = state.chain_for_phase(phase)
        if chain is None:
            return state
        return state.with_phase_chain(phase, chain.with_retry_increment())

    def _apply_chain_retry(
        self,
        state: PipelineState,
        phase: str,
        chain: AgentChainState,
        *,
        retry_in_session: bool,
    ) -> PipelineState:
        """Apply a single retry to the chain and optionally preserve the agent session."""
        retried_state = state.with_phase_chain(phase, chain.with_retry_increment())
        if retry_in_session and state.last_agent_session_id:
            retried_state = retried_state.copy_with(session_preserve_retry_pending=True)
        return retried_state

    def _chain_config_for_phase(self, phase: str) -> AgentChainConfig | None:
        """Resolve the AgentChainConfig backing the given phase, or None."""
        if self._policy_bundle is None:
            return None
        phase_def = self._policy_bundle.pipeline.phases.get(phase)
        if phase_def is None:
            return None
        drain_config = self._policy_bundle.agents.agent_drains.get(phase_def.drain)
        if drain_config is None:
            return None
        return self._policy_bundle.agents.agent_chains.get(drain_config.chain)

    def _compute_retry_delay(
        self,
        phase: str,
        agent: str | None,
    ) -> int:
        """Compute the retry delay for a given phase and agent.

        Uses the chain's retry_delay_ms from policy configuration.
        """
        chain_config = self._chain_config_for_phase(phase)
        if chain_config is None:
            return 0

        # Get backoff attempt count for this phase:agent
        key = f"{phase}:{agent}" if agent else phase
        attempt = self._backoff_attempts.get(key, 0)

        return compute_backoff_ms(chain_config.retry_delay_ms, attempt)

    def reset_backoff(self, phase: str, agent: str | None) -> None:
        """Reset backoff counter for a phase/agent after successful invocation."""
        key = f"{phase}:{agent}" if agent else phase
        self._backoff_attempts.pop(key, None)

    def _write_session_reset_hint(
        self,
        phase: str,
        failure: ClassifiedFailure,
    ) -> None:
        """Write a retry hint file describing the stale-session failure.

        Args:
            phase: Pipeline phase where the failure occurred.
            failure: Classified failure with stale-session detail.
        """
        # Lazy imports: ralph.phases depends transitively on ralph.recovery.
        from pathlib import Path  # noqa: PLC0415

        from ralph.phases.required_artifacts import (  # noqa: PLC0415
            build_retry_hint,
            retry_hint_path,
        )

        detail = (
            "Previous session id was invalid; restart with fresh session."
            f" Original failure: {failure.raw_message}"
        )
        hint_content = build_retry_hint(phase, detail)
        hint_file = Path(retry_hint_path(phase))
        try:
            hint_file.parent.mkdir(parents=True, exist_ok=True)
            hint_file.write_text(hint_content, encoding="utf-8")
        except OSError:
            logger.warning("Failed to write session reset hint to {}", hint_file)

    def _handle_agent_budget_exhaustion(
        self,
        state: PipelineState,
        failure: ClassifiedFailure,
        phase: str,
        agent: str | None,
        *,
        retry_in_session: bool = False,
    ) -> tuple[PipelineState, list[Effect]]:
        """Handle agent failure with budget debit and chain progression."""
        # Lazy import: ralph.pipeline.state depends transitively on recovery types.
        from ralph.pipeline.state import FalloverRecord  # noqa: PLC0415

        chain = state.chain_for_phase(phase)
        if chain is None:
            return state, []

        current_agent = agent or (
            chain.agents[chain.current_index]
            if chain.agents and chain.current_index < len(chain.agents)
            else None
        )

        # Get max_retries from policy if available
        max_retries = self._get_max_retries_for_chain(phase)

        budget_state = (
            self._registry.get(phase, current_agent) if current_agent is not None else None
        )
        should_retry_in_chain = current_agent is not None and (
            (budget_state is not None and not budget_state.exhausted)
            or (budget_state is None and chain.retries < max_retries)
        )
        if should_retry_in_chain:
            return (
                self._apply_chain_retry(state, phase, chain, retry_in_session=retry_in_session),
                [],
            )

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

            new_state = (
                state.with_phase_chain(phase, chain.with_advance())
                .copy_with(
                    # Fallover: reset retry delay for new agent
                    last_retry_delay_ms=0,
                )
                .with_fallover_record(fallover_record)
            )
            return new_state, []

        new_state = state.copy_with(
            recovery_cycle_count=state.recovery_cycle_count + 1,
        )
        failed_state = self._enter_phase_failed(new_state, failure.reason, failure.category)
        return failed_state, []

    def _get_max_retries_for_chain(self, phase: str) -> int:
        """Get max_retries from policy for the chain used by this phase."""
        chain_config = self._chain_config_for_phase(phase)
        if chain_config is None:
            return 3
        return chain_config.max_retries

    def snapshot(self) -> dict[str, object]:
        """Return a runtime observability snapshot of recovery state."""
        return {
            "cycle_cap": self._cap.cap,
            "budgets": {
                f"{phase}:{agent}": {
                    "max_retries": budget.max_retries,
                    "consumed": budget.consumed,
                    "remaining": budget.remaining,
                    "exhausted": budget.exhausted,
                }
                for (phase, agent), budget in self._registry.items()
            },
            "backoff_attempts": dict(self._backoff_attempts),
        }

    def _enter_phase_failed(
        self,
        state: PipelineState,
        reason: str,
        category: object,
    ) -> PipelineState:
        """Enter the terminal failure phase.

        Uses policy.declared.failed_route when available, raising a RuntimeError
        if policy is not set (signals missing policy at a routing call site).
        """
        if self._policy_bundle is None:
            raise RuntimeError(
                "_enter_phase_failed requires policy_bundle to be set on the controller. "
                "Without policy, the runtime cannot determine the failure route. "
                "Set policy_bundle when constructing RecoveryController."
            )
        failed_route = self._policy_bundle.pipeline.recovery.failed_route
        return state.copy_with(
            phase=failed_route,
            previous_phase=state.phase,
            last_error=reason,
            recovery_epoch=state.recovery_epoch + 1,
            last_failure_category=str(category),
            last_retry_delay_ms=0,
        )
