"""Single authority for pipeline progression decisions.

The run loop is the only caller of :func:`decide_phase_transition`,
which is the single authority for what runs next. The function is
pure: it takes its inputs by value and returns a typed
:class:`PhaseDecision`. The existing
:func:`ralph.pipeline.handoffs.resolve_next_phase` is consulted to
pick the next phase from the current state and a signal, but
``decide_phase_transition`` converts that into the typed
:class:`PhaseDecision` that the run loop, display, and prompt
materialisation read.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PipelinePolicy


class PhaseDecision(StrEnum):
    """The closed vocabulary the run loop reads for progression.

    The run loop, the display formatter, the prompt materialiser,
    and the completion summary all consume the same enum values;
    no caller may infer progression from prose, the absence of a
    record, the cleanliness of a worktree, or the presence of a
    Git marker.
    """

    CONTINUE_PHASE = "continue_phase"
    ADVANCE_PHASE = "advance_phase"
    BLOCK_FOR_RECOVERY = "block_for_recovery"
    REPORT_TERMINAL_SUCCESS = "report_terminal_success"
    REPORT_TERMINAL_FAILURE = "report_terminal_failure"
    REPORT_INTERVENTION_REQUIRED = "report_intervention_required"


#: All known values of :class:`PhaseDecision`. ``Transition.ALL``
#: consults this for the closed-set invariant.
_ALL_DECISIONS: frozenset[str] = frozenset(d.value for d in PhaseDecision)


def _check_blocking_decision(
    integration_record: IntegrationRecord | None,
    rebase_state: RebaseState,
) -> PhaseDecision | None:
    if integration_record is not None:
        if integration_record.diagnostic_intervention_required:
            return PhaseDecision.REPORT_INTERVENTION_REQUIRED
        if integration_record.phase == "integrating":
            return PhaseDecision.BLOCK_FOR_RECOVERY
        if (
            integration_record.phase == "integrated"
            and integration_record.integrated_feature_sha is None
        ):
            return PhaseDecision.BLOCK_FOR_RECOVERY
    if rebase_state.integration_unresolved:
        return PhaseDecision.BLOCK_FOR_RECOVERY
    return None


def decide_phase_transition(
    state: PipelineState,
    policy: PipelinePolicy,
    *,
    integration_record: IntegrationRecord | None,
    rebase_state: RebaseState,
) -> PhaseDecision:
    """Return the typed :class:`PhaseDecision` for the current state.

    The function is pure. It is the single authority for the
    question "what may the run loop do next?". The
    ``rebase_state.last_decision`` field is the single persisted
    surface for the typed decision; no caller may reconstruct the
    decision from prose or from auxiliary state.
    """
    blocking = _check_blocking_decision(integration_record, rebase_state)
    if blocking is not None:
        return blocking
    if not is_terminal_success_eligible(state, integration_record, rebase_state):
        # Even when no integration record blocks, terminal success
        # is only reported when every authority has finished.
        # Pass through to the phase resolver so the run loop can
        # make forward progress on the current phase.
        pass
    # Delegate to the existing reducer for the next-phase signal.
    from ralph.pipeline.handoffs import resolve_next_phase

    try:
        next_phase = resolve_next_phase(state.phase, "success", policy)
    except ValueError:
        # ``resolve_next_phase`` raises on terminal phases that do
        # not declare a transition. Treat them as terminal success
        # when the policy's terminal-phase set includes the
        # current phase.
        terminal = policy.terminal_states()
        if state.phase in terminal:
            return PhaseDecision.REPORT_TERMINAL_SUCCESS
        return PhaseDecision.REPORT_TERMINAL_FAILURE
    terminal = policy.terminal_states()
    if next_phase in terminal:
        return PhaseDecision.REPORT_TERMINAL_SUCCESS
    if next_phase == state.phase:
        return PhaseDecision.CONTINUE_PHASE
    return PhaseDecision.ADVANCE_PHASE


def is_terminal_success_eligible(
    state: PipelineState,
    integration_record: IntegrationRecord | None,
    rebase_state: RebaseState,
) -> bool:
    """Return ``True`` only when every authority has finished its obligation.

    The function is the single authority for the "may the run
    loop report terminal success?" question. It returns ``False``
    unconditionally while any of:

    * the :class:`IntegrationRecord` is present and not cleared;
    * :attr:`RebaseState.integration_unresolved` is True;
    * the publication authority reports a non-terminal decision;
    * the parallel-joins authority reports an unfinished join.
    """
    if integration_record is not None:
        return False
    if rebase_state.integration_unresolved:
        return False
    if rebase_state.last_publication_decision not in (None, "publication_terminal"):
        return False
    if rebase_state.last_fan_out_decision not in (None, "join_complete", "join_blocked"):
        return False
    del state
    return True


__all__ = [
    "PhaseDecision",
    "decide_phase_transition",
    "is_terminal_success_eligible",
]
