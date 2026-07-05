"""State mutation helpers for the pipeline runner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline import progress
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
    """Reset the target phase chain when re-entering after the terminal failure route."""
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
    """Recover from a missing plan handoff by routing back to the entry phase.

    When ``current_epoch >= pipeline_policy.recovery.cycle_cap``, the recovery
    routes to ``pipeline_policy.recovery.failed_route`` (default
    ``"failed_terminal"``) instead of ``entry_phase``, preventing an infinite
    recovery loop when a planning agent persistently fails to produce a plan
    artifact. On the success path (below the bound) the helper advances to
    ``pipeline_policy.entry_phase``. Both paths preserve
    ``last_error=str(exc)`` so the operator sees the underlying
    ``MissingPlanHandoffError`` message regardless of which branch fires,
    matching the ``ExitFailureEffect`` convention.

    On the success path (rerouting to ``entry_phase``) the helper ALSO
    resets the target entry phase's ``AgentChainState`` via
    ``reset_phase_chain_for_recovery`` so the recovered planning pass
    starts with ``current_index=0`` and ``retries=0`` rather than
    resuming on a fallback planner or with retry debt carried over from
    the prior planning attempt. This mirrors the chain-reset behaviour
    already used by the failed-route re-entry branch in
    ``ralph/pipeline/runner.py`` so every re-entry into a phase starts
    a fresh chain. The bound-exceeded path does NOT reset the chain:
    the pipeline is heading to ``failed_route`` (terminal), so the
    planning chain state is irrelevant on that branch.

    Mirrors the side-effect order of the original inline ``except`` blocks in
    ``_handle_inline_effect`` and ``_run_pipeline_step``:

    1. ``logger.warning`` (success path) or ``logger.error`` (bound-exceeded
       path) with the missing handoff message.
    2. ``progress.advance_phase`` to ``entry_phase`` (success) or
       ``failed_route`` (bound-exceeded).
    3. ``reset_phase_chain_for_recovery(state, target_phase)`` on the
       success path only, so the recovered planning phase restarts its
       agent chain from scratch.
    4. ``copy_with(last_error=str(exc), recovery_epoch=current_epoch + 1)``.
    5. ``ckpt.save(recovered_state, checkpoint_path)``.
    6. ``_notify_pipeline_subscriber(subscriber, recovered_state)``.

    The on-disk checkpoint write happens before the subscriber is notified
    so the next run can resume from the recovered state even if the
    subscriber callback raises.

    Returns the recovered ``PipelineState``. Callers MUST return the
    recovered state to their caller (do not continue the current effect).
    """
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

    if not bound_exceeded:
        advanced_state = reset_phase_chain_for_recovery(
            advanced_state, target_phase
        )

    recovered_state = advanced_state.copy_with(
        last_error=str(exc),
        recovery_epoch=current_epoch + 1,
    )
    ckpt.save(recovered_state, checkpoint_path)
    notify_pipeline_subscriber(subscriber, recovered_state)
    return recovered_state
