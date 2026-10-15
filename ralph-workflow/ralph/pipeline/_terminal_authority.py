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
    pub = getattr(rebase_state, "last_publication_decision", None)
    fan_out = getattr(rebase_state, "last_fan_out_decision", None)
    if (
        integration_record is not None
        or getattr(rebase_state, "integration_unresolved", False) is True
        or getattr(rebase_state, "recovery_record_retained", False) is True
        or getattr(rebase_state, "is_intervention_required", False) is True
        or (isinstance(pub, str) and pub != "publication_terminal")
        or (isinstance(fan_out, str) and fan_out != "join_complete")
    ):
        return False
    return True


__all__ = ["is_terminal_success_eligible"]
