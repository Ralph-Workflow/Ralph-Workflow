
from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline import progress
from ralph.pipeline.cycle_timing import conclude_cycle_on_route_out_of_cycle
from ralph.pipeline.state import AgentChainState

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Protocol

    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PipelinePolicy

    class _NotifiableSubscriber(Protocol):
        def notify(self, state: PipelineState) -> None: ...


def notify_dashboard_subscriber(
    dashboard_subscriber: _NotifiableSubscriber | None,
    state: PipelineState,
) -> None:
    if dashboard_subscriber is None:
        return
    dashboard_subscriber.notify(state)


def notify_pipeline_subscriber(
    pipeline_subscriber: _NotifiableSubscriber | None,
    state: PipelineState,
) -> None:
    notify_dashboard_subscriber(pipeline_subscriber, state)


def reset_phase_chain_for_recovery(
    state: PipelineState,
    target_phase: str,
) -> PipelineState:
    chain = state.chain_for_phase(target_phase)
    if chain is None:
        return state
    return state.with_phase_chain(
        target_phase,
        AgentChainState(agents=chain.agents, current_index=0, retries=0),
    )


def recover_missing_plan_handoff(
    *,
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
    checkpoint_path: Path,
    subscriber: _NotifiableSubscriber | None,
    exc: Exception,
) -> PipelineState:
    current_epoch = state.recovery_epoch if isinstance(state.recovery_epoch, int) else 0
    cycle_cap = pipeline_policy.recovery.cycle_cap
    bound_exceeded = current_epoch >= cycle_cap

    if bound_exceeded:
        logger.error(
            "Missing plan handoff for phase={phase}: {err}; recovery_epoch={epoch} "
            "has reached cycle_cap={cap}, routing to failed_route={route}",
            phase=state.phase,
            err=exc,
            epoch=current_epoch,
            cap=cycle_cap,
            route=pipeline_policy.recovery.failed_route,
        )
        target_phase = pipeline_policy.recovery.failed_route
    else:
        logger.warning(
            "Missing plan handoff for phase={phase}: {err}; re-routing to entry phase",
            phase=state.phase,
            err=exc,
        )
        target_phase = pipeline_policy.entry_phase

    advanced_state = progress.advance_phase(
        state,
        target_phase,
        policy=pipeline_policy,
    )
    advanced_state = conclude_cycle_on_route_out_of_cycle(
        advanced_state, target_phase, policy=pipeline_policy
    )

    if not bound_exceeded:
        advanced_state = reset_phase_chain_for_recovery(advanced_state, target_phase)

    recovered_state = advanced_state.copy_with(
        last_error=str(exc),
        recovery_epoch=current_epoch + 1,
    )
    ckpt.save(recovered_state, checkpoint_path)
    notify_pipeline_subscriber(subscriber, recovered_state)
    return recovered_state
