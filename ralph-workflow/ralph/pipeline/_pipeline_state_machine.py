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

from ralph.pipeline._terminal_authority import is_terminal_success_eligible

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
    if rebase_state.last_decision in (
        PhaseDecision.BLOCK_FOR_RECOVERY.value,
        PhaseDecision.REPORT_INTERVENTION_REQUIRED.value,
    ):
        return PhaseDecision(rebase_state.last_decision)
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


def _is_terminal_failure(phase: str, policy: PipelinePolicy | None) -> bool:
    if policy is None:
        return False
    try:
        from ralph.pipeline.reducer import _terminal_failure_route

        return phase == _terminal_failure_route(policy)
    except Exception:
        return False


def decide_phase_transition(
    state: PipelineState,
    policy: PipelinePolicy | None = None,
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
    if policy is None:
        return PhaseDecision.CONTINUE_PHASE

    terminal: set[str] = set()
    if hasattr(policy, "terminal_states"):
        terminal.update(policy.terminal_states())
    if hasattr(policy, "terminal_phase") and policy.terminal_phase:
        terminal.add(policy.terminal_phase)

    # Delegate to the existing reducer for the next-phase signal.
    from ralph.pipeline.handoffs import resolve_next_phase

    candidate_phase: str | None = None
    try:
        candidate_phase = resolve_next_phase(state.phase, "success", policy)
    except ValueError:
        if state.phase in terminal:
            candidate_phase = state.phase

    decision: PhaseDecision
    if candidate_phase is None:
        decision = PhaseDecision.REPORT_TERMINAL_FAILURE
    elif candidate_phase in terminal:
        if _is_terminal_failure(candidate_phase, policy):
            decision = PhaseDecision.REPORT_TERMINAL_FAILURE
        elif not is_terminal_success_eligible(state, integration_record, rebase_state):
            decision = PhaseDecision.BLOCK_FOR_RECOVERY
        else:
            decision = PhaseDecision.REPORT_TERMINAL_SUCCESS
    elif candidate_phase == state.phase:
        decision = PhaseDecision.CONTINUE_PHASE
    else:
        decision = PhaseDecision.ADVANCE_PHASE

    return decision


__all__ = [
    "PhaseDecision",
    "decide_phase_transition",
    "is_terminal_success_eligible",
]
