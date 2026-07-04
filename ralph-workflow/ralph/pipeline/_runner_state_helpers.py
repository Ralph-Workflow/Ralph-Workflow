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

    Mirrors the byte-identical side-effect order of the original inline
    ``except MissingPlanHandoffError`` block in
    ``_handle_inline_effect`` and the parallel call site in
    ``_run_pipeline_step``:

    1. ``logger.warning`` with the missing handoff message.
    2. ``progress.advance_phase`` to ``pipeline_policy.entry_phase``.
    3. ``copy_with(last_error=str(exc), recovery_epoch=current_epoch + 1)``.
    4. ``ckpt.save(recovered_state, checkpoint_path)``.
    5. ``_notify_pipeline_subscriber(subscriber, recovered_state)``.

    The on-disk checkpoint write happens before the subscriber is notified
    so the next run can resume from the recovered state even if the
    subscriber callback raises.

    Returns the recovered ``PipelineState``. Callers MUST return the
    recovered state to their caller (do not continue the current effect).
    """
    logger.warning(
        "Missing plan handoff for phase={phase}: {err}; re-routing to entry phase",
        phase=state.phase,
        err=exc,
    )
    current_epoch = state.recovery_epoch if isinstance(state.recovery_epoch, int) else 0
    recovered_state = progress.advance_phase(
        state,
        pipeline_policy.entry_phase,
        policy=pipeline_policy,
    ).copy_with(
        last_error=str(exc),
        recovery_epoch=current_epoch + 1,
    )
    ckpt.save(recovered_state, checkpoint_path)
    notify_pipeline_subscriber(subscriber, recovered_state)
    return recovered_state
