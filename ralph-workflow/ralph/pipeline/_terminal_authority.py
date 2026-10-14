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
    if integration_record is not None:
        return False
    if rebase_state.integration_unresolved:
        return False
    if rebase_state.last_publication_decision not in (None, "publication_terminal"):
        return False
    if rebase_state.last_fan_out_decision not in (
        None,
        "join_complete",
    ):
        return False
    del state
    return True


__all__ = ["is_terminal_success_eligible"]
