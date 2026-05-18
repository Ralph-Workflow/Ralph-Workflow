"""State mutation helpers for the pipeline runner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.state import AgentChainState

if TYPE_CHECKING:
    from ralph.pipeline.state import PipelineState

    class _NotifiableSubscriber:
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
