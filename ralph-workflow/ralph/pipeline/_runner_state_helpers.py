from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline import progress
from ralph.pipeline.cycle_timing import conclude_cycle_on_route_out_of_cycle
from ralph.pipeline.no_progress_reroute import (
    RerouteAction,
    clear_phase_continuation,
    no_progress_failure_reason,
    record_reroute,
    recovery_cap_failure_reason,
    reroute_signature,
    with_reroute_recorded,
)
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
    workspace_root: Path,
    subscriber: _NotifiableSubscriber | None,
    exc: Exception,
) -> PipelineState:
    current_epoch = state.recovery_epoch if isinstance(state.recovery_epoch, int) else 0
    cycle_cap = pipeline_policy.recovery.cycle_cap
    failing_phase = str(state.phase)
    cause = str(exc)
    entry_phase = pipeline_policy.entry_phase
    signature = reroute_signature(failing_phase, entry_phase, cause)
    action, reroute_count = record_reroute(state, signature)
    failure_reason: str | None = None

    if current_epoch >= cycle_cap:
        failure_reason = recovery_cap_failure_reason(failing_phase, cause, cycle_cap)
    elif action is RerouteAction.FAIL:
        failure_reason = no_progress_failure_reason(failing_phase, cause, reroute_count)

    if failure_reason is not None:
        logger.error(
            "Missing plan handoff: {reason}; routing to failed_route={route}",
            reason=failure_reason,
            route=pipeline_policy.recovery.failed_route,
        )
        target_phase = pipeline_policy.recovery.failed_route
    elif action is RerouteAction.FRESH_ENTRY:
        logger.warning(
            "Missing plan handoff for phase={phase}: {err}; {count} identical re-routes "
            "invoked no agent, clearing continuation state and re-entering {target} fresh",
            phase=failing_phase,
            err=exc,
            count=reroute_count,
            target=entry_phase,
        )
        clear_phase_continuation(
            workspace_root,
            (entry_phase,) if failing_phase == entry_phase else (entry_phase, failing_phase),
            pipeline_policy,
        )
        target_phase = entry_phase
    else:
        logger.warning(
            "Missing plan handoff for phase={phase}: {err}; re-routing to entry phase",
            phase=failing_phase,
            err=exc,
        )
        target_phase = entry_phase

    advanced_state = progress.advance_phase(
        state,
        target_phase,
        policy=pipeline_policy,
    )
    advanced_state = conclude_cycle_on_route_out_of_cycle(
        advanced_state, target_phase, policy=pipeline_policy
    )

    if failure_reason is None:
        advanced_state = reset_phase_chain_for_recovery(advanced_state, target_phase)

    recovered_state = with_reroute_recorded(
        advanced_state.copy_with(
            last_error=failure_reason or cause,
            recovery_epoch=current_epoch + 1,
        ),
        signature,
        reroute_count,
    )
    if failure_reason is not None:
        recovered_state = recovered_state.clear_waiting()
    ckpt.save(recovered_state, checkpoint_path)
    notify_pipeline_subscriber(subscriber, recovered_state)
    return recovered_state
