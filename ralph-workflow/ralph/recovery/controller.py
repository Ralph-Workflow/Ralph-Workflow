"""RecoveryController: single owner of failure classification, budget, and fallover."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.timeout_clock import Clock, SystemClock
from ralph.pipeline import progress
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_retry_intent import (
    cleared_agent_retry_intent,
    resume_agent_retry_intent,
)
from ralph.pipeline.effects import ExitFailureEffect
from ralph.pipeline.state import FalloverRecord
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.classifier import (
    ClassifiedFailure,
    FailureCategory,
    FailureClassifier,
    FailureContext,
)
from ralph.recovery.cycle_cap import CycleCap
from ralph.recovery.events import FailureEvent, FailureEventBus, FalloverEvent
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions

__all__ = ["RecoveryController", "RecoveryControllerOptions", "compute_backoff_ms"]

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.effects import Effect
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import AgentChainConfig


def _build_exit_failure_effect(*, reason: str) -> Effect:
    return ExitFailureEffect(reason=reason)


def _build_fallover_record(
    *,
    phase: str,
    from_agent: str,
    to_agent: str,
    timestamp_iso: str,
) -> FalloverRecord:
    return FalloverRecord(
        phase=phase,
        from_agent=from_agent,
        to_agent=to_agent,
        timestamp_iso=timestamp_iso,
    )


def _get_required_artifact_helpers() -> tuple[Callable[[str, str], str], Callable[[str], str]]:
    # Lazy import to avoid circular dependency via ralph.phases import chain
    module = import_module("ralph.phases.required_artifacts")
    namespace = cast("dict[str, object]", module.__dict__)
    build_retry_hint = cast("Callable[[str, str], str]", namespace["build_retry_hint"])
    retry_hint_path = cast("Callable[[str], str]", namespace["retry_hint_path"])
    return build_retry_hint, retry_hint_path


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

    def __init__(
        self,
        *,
        options: RecoveryControllerOptions | None = None,
    ) -> None:
        opts = options or RecoveryControllerOptions()
        self._cap = CycleCap(cap=opts.cycle_cap)
        self._classifier = opts.classifier or FailureClassifier()
        self._bus = opts.event_bus or FailureEventBus()
        self._registry = opts.budget_registry or AgentBudgetRegistry()
        self._policy_bundle = opts.policy_bundle
        self._backoff_attempts: dict[str, int] = opts.backoff_attempts or {}
        self._technical_retry_cap = max(0, opts.technical_retry_cap)
        self._unavailable_timeouts: dict[str, int] = dict(opts.unavailable_timeouts or {})
        self._clock: Clock = opts.clock or SystemClock()

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
        context: FailureContext,
    ) -> tuple[PipelineState, list[Effect], FailureEvent]:
        """Classify a failure and compute the recovery transition.

        Args:
            state: Current pipeline state.
            raw_failure: The raw exception or string error message.
            context: Phase/agent context and optional pre-classified failure.

        Returns:
            Tuple of (new_state, effects, failure_event).
        """
        phase = context.phase
        agent = context.agent
        retry_in_session = context.retry_in_session
        failure = context.classified_failure or self._classifier.classify(
            raw_failure,
            phase=phase,
            agent=agent,
            connectivity_state=state.last_connectivity_state,
        )

        chain = state.chain_for_phase(phase)
        chain_capacity = 0
        retry_delay_ms = 0
        is_agent_unavailable = failure.is_unavailable and agent is not None

        if chain is not None:
            chain_capacity = max(0, len(chain.agents) - chain.current_index - 1)

            # Compute retry delay from chain config only when the failing
            # agent is currently available. An unavailable agent is skipped
            # (not retried), so no retry delay is charged for it.
            if (
                agent is not None
                and failure.counts_against_budget
                and not is_agent_unavailable
            ):
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
            new_state = new_state.copy_with(last_error=failure.reason)
            new_state, effects, failure_evt = self._handle_technical_retry_exhaustion(
                new_state,
                failure,
                phase,
                agent,
                retry_in_session=retry_in_session,
                failure_evt=failure_evt,
            )
            return new_state, effects, failure_evt

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
            new_state = new_state.copy_with(last_error=failure.reason)
            new_state, effects, failure_evt = self._handle_technical_retry_exhaustion(
                new_state,
                failure,
                phase,
                agent,
                retry_in_session=retry_in_session,
                failure_evt=failure_evt,
            )
            return new_state, effects, failure_evt

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
        if failure.is_unavailable and agent is not None:
            self._mark_agent_unavailable(phase, agent)

        if failure.reset_session:
            logger.warning(
                "Stale session detected in phase={} (session id invalid): {}",
                phase,
                failure.reason[:200],
            )
            new_state = new_state.copy_with(
                last_agent_session_id=None,
                agent_retry_intent=cleared_agent_retry_intent(),
            )
            self._write_session_reset_hint(phase, failure)

        if agent is not None and not is_agent_unavailable:
            self._registry = self._registry.debit(phase, agent, failure)
            # Track backoff attempt for retry-delay growth on non-unavailable
            # agent failures. Unavailable attempts are tracked inside
            # _mark_agent_unavailable because they use a separate cooldown.
            if failure.counts_against_budget:
                key = f"{phase}:{agent}"
                self._backoff_attempts[key] = self._backoff_attempts.get(key, 0) + 1

        new_state, effects, failure_evt = self._handle_agent_budget_exhaustion(
            new_state,
            failure,
            phase,
            agent,
            retry_in_session=retry_in_session,
            failure_evt=failure_evt,
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
                [_build_exit_failure_effect(reason=exit_reason)],
                failure_evt,
            )

        return new_state, effects, failure_evt

    def _mark_agent_unavailable(
        self,
        phase: str,
        agent: str,
        base_backoff_ms: int = 5_000,
    ) -> int:
        """Mark an agent as unavailable until the computed backoff expires.

        Uses exponential backoff based on the current attempt count for the
        phase:agent pair, capped at ``max_backoff_ms``. Returns the computed
        backoff in milliseconds and stores the expiration timestamp (ms).
        """
        key = f"{phase}:{agent}"
        attempt = self._backoff_attempts.get(key, 0)
        backoff_ms = compute_backoff_ms(base_backoff_ms, attempt, max_ms=300_000)
        current_time_ms = int(self._clock.monotonic() * 1000)
        self._unavailable_timeouts[key] = current_time_ms + backoff_ms
        self._backoff_attempts[key] = attempt + 1
        return backoff_ms

    def _is_agent_available(self, phase: str, agent: str) -> bool:
        """Return True when the agent is not currently marked unavailable."""
        key = f"{phase}:{agent}"
        unavailable_until_ms = self._unavailable_timeouts.get(key, 0)
        current_time_ms = int(self._clock.monotonic() * 1000)
        return current_time_ms >= unavailable_until_ms

    def _increment_chain_retries(self, state: PipelineState, phase: str) -> PipelineState:
        """Increment chain.retries for the given phase without debiting the budget."""
        chain = state.chain_for_phase(phase)
        if chain is None:
            return state
        return state.with_phase_chain(phase, chain.with_retry_increment())

    def _handle_technical_retry_exhaustion(
        self,
        state: PipelineState,
        failure: ClassifiedFailure,
        phase: str,
        agent: str | None,
        *,
        retry_in_session: bool = False,
        failure_evt: FailureEvent,
    ) -> tuple[PipelineState, list[Effect], FailureEvent]:
        return self._handle_retry_progression(
            state,
            failure,
            phase,
            agent,
            retry_in_session=retry_in_session,
            max_retries=self._technical_retry_cap,
            use_budget=False,
            failure_evt=failure_evt,
        )

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
            return retried_state.copy_with(
                agent_retry_intent=resume_agent_retry_intent(
                    state.last_agent_session_id,
                ),
            )
        return retried_state.copy_with(agent_retry_intent=cleared_agent_retry_intent())

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
        build_retry_hint, retry_hint_path = _get_required_artifact_helpers()

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
        failure_evt: FailureEvent,
    ) -> tuple[PipelineState, list[Effect], FailureEvent]:
        """Handle agent failure with budget debit and chain progression."""
        return self._handle_retry_progression(
            state,
            failure,
            phase,
            agent,
            retry_in_session=retry_in_session,
            max_retries=self._get_max_retries_for_chain(phase),
            use_budget=True,
            failure_evt=failure_evt,
        )

    def _handle_retry_progression(
        self,
        state: PipelineState,
        failure: ClassifiedFailure,
        phase: str,
        agent: str | None,
        *,
        retry_in_session: bool,
        max_retries: int,
        use_budget: bool,
        failure_evt: FailureEvent,
    ) -> tuple[PipelineState, list[Effect], FailureEvent]:
        chain = state.chain_for_phase(phase)
        if chain is None:
            return state, [], failure_evt

        current_agent = agent or (
            chain.agents[chain.current_index]
            if chain.agents and chain.current_index < len(chain.agents)
            else None
        )
        budget_state = (
            self._registry.get(phase, current_agent)
            if use_budget and current_agent is not None
            else None
        )
        is_agent_unavailable = failure.is_unavailable and agent is not None
        next_available_index = self._next_available_agent_index(
            chain, phase, wrap=is_agent_unavailable
        )
        current_agent_available = current_agent is None or self._is_agent_available(
            phase, current_agent
        )
        # Retry the current agent only when it is actually available. An agent
        # marked unavailable (cooldown/backoff) must never be retried, even if
        # it is the only agent in the chain. If no agent is available, the chain
        # is exhausted and we fail the phase.
        can_retry_current = current_agent_available
        should_retry_in_chain = (
            current_agent is not None
            and can_retry_current
            and (
                (budget_state is not None and not budget_state.exhausted)
                or (budget_state is None and chain.retries < max_retries)
            )
        )
        if should_retry_in_chain:
            return (
                self._apply_chain_retry(state, phase, chain, retry_in_session=retry_in_session),
                [],
                failure_evt,
            )

        if next_available_index is not None:
            next_agent = chain.agents[next_available_index]
            from_agent = current_agent or f"agent[{chain.current_index}]"
            fallover_record = _build_fallover_record(
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
                current_index=next_available_index,
                retries=0,
            )
            new_state = (
                state.with_phase_chain(phase, new_chain)
                .copy_with(
                    last_retry_delay_ms=0,
                    last_agent_session_id=None,
                    agent_retry_intent=cleared_agent_retry_intent(),
                )
                .with_fallover_record(fallover_record)
            )
            return new_state, [], failure_evt

        # If every agent in the chain is temporarily unavailable, preserve the
        # session and wait until the earliest cooldown expires instead of
        # terminating the run. The run loop will sleep on last_retry_delay_ms
        # and then retry the same phase. Otherwise the chain is truly exhausted
        # (available agents have no budget/retries left), so fail the phase.
        if all(
            not self._is_agent_available(phase, agent) for agent in chain.agents
        ):
            wait_ms = self._earliest_unavailable_wait_ms(phase, chain)
            reason = "all agents unavailable; waiting for cooldown expiry"
            logger.info("{} in phase={} (wait_ms={})", reason, phase, wait_ms)
            updated_evt = replace(failure_evt, retry_delay_ms=wait_ms)
            self._bus.publish(updated_evt)
            return (
                state.copy_with(
                    last_error=reason,
                    last_retry_delay_ms=wait_ms,
                ),
                [],
                updated_evt,
            )

        new_state = state.copy_with(recovery_cycle_count=state.recovery_cycle_count + 1)
        failed_state = self._enter_phase_failed(new_state, failure.reason, failure.category)
        return failed_state, [], failure_evt

    def _earliest_unavailable_wait_ms(
        self,
        phase: str,
        chain: AgentChainState,
    ) -> int:
        """Return milliseconds until the first unavailable agent in the chain becomes available."""
        current_time_ms = int(self._clock.monotonic() * 1000)
        min_remaining: int | None = None
        for agent in chain.agents:
            until_ms = self._unavailable_timeouts.get(f"{phase}:{agent}", 0)
            if until_ms > current_time_ms:
                remaining = until_ms - current_time_ms
                if min_remaining is None or remaining < min_remaining:
                    min_remaining = remaining
        return max(0, min_remaining or 0)

    def _next_available_agent_index(
        self,
        chain: AgentChainState,
        phase: str,
        *,
        wrap: bool = False,
    ) -> int | None:
        """Return the index of the next available agent in chain order.

        By default only agents after the current index are considered, preserving
        the existing forward-only fallover semantics for budget exhaustion. When
        ``wrap=True`` the search is cyclic, so earlier agents whose unavailable
        cooldown has expired can be reconsidered. The current agent itself is
        never returned here; it is handled by the retry-current logic.
        """
        n = len(chain.agents)
        if n <= 1:
            return None
        max_offset = n if wrap else n - chain.current_index
        for offset in range(1, max_offset):
            index = (chain.current_index + offset) % n
            if self._is_agent_available(phase, chain.agents[index]):
                return index
        return None

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
            "technical_retry_cap": self._technical_retry_cap,
            "unavailable_timeouts": dict(self._unavailable_timeouts),
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
        return progress.advance_phase(
            state,
            failed_route,
            policy=self._policy_bundle.pipeline,
        ).copy_with(
            last_error=reason,
            recovery_epoch=state.recovery_epoch + 1,
            last_failure_category=str(category),
            last_retry_delay_ms=0,
        )
