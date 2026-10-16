"""Single authority for terminal-success eligibility.

The terminal authority is the only component permitted to return
``True`` for a terminal-success report. The function returns
``False`` unconditionally while any of:

* an :class:`IntegrationRecord` exists and is not cleared;
* :attr:`RebaseState.integration_unresolved` is True;
* the publication authority reports a non-terminal decision;
* the parallel-joins authority reports an unfinished join.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline._fan_out_authority import FanOutDecision
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
    """Return ``True`` only when every authority has finished its obligation.

    The function is the single authority for the "may the run
    loop report terminal success?" question.
    """
    del state
    pub = rebase_state.last_publication_decision
    fan_out = rebase_state.last_fan_out_decision
    return not (
        integration_record is not None
        or rebase_state.integration_unresolved is True
        or rebase_state.recovery_record_retained is True
        or rebase_state.is_intervention_required is True
        or (pub is not None and pub != PublicationDecision.PUBLICATION_TERMINAL)
        or (
            fan_out is not None
            and fan_out != FanOutDecision.JOIN_COMPLETE
        )
    )


__all__ = ["is_terminal_success_eligible"]
