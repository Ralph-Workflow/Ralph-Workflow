
from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline._fan_out_authority import FanOutDecision, fan_out_decision
from ralph.pipeline._publication_state_machine import PublicationDecision

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.pipeline.state import PipelineState


def is_terminal_success_eligible(
    state: PipelineState | None,
    integration_record: IntegrationRecord | None,
    rebase_state: RebaseState,
) -> bool:
    pub = rebase_state.last_publication_decision
    cached_fan_out = rebase_state.last_fan_out_decision
    if state is None:
        live_fan_out = cached_fan_out
    else:
        canonical = fan_out_decision(state)
        live_fan_out = (
            canonical
            if cached_fan_out is None or cached_fan_out == canonical
            else FanOutDecision.JOIN_BLOCKED
        )
    return not (
        integration_record is not None
        or rebase_state.integration_unresolved is True
        or rebase_state.recovery_record_retained is True
        or rebase_state.is_intervention_required is True
        or (pub is not None and pub != PublicationDecision.PUBLICATION_TERMINAL)
        or (live_fan_out is not None and live_fan_out != FanOutDecision.JOIN_COMPLETE)
    )


__all__ = ["is_terminal_success_eligible"]
