"""Judge one resolution round from the evidence, and name what ended it.

An agent has no way to REPORT that a conflict is unresolvable, so every
verdict here is taken from the session record and from the files -- never
from the resolver's own opinion. That is why an emptied file counts as
unresolved: a truncating resolver drops both sides, and with the markers
gone a marker scan has nothing left to object to.

The naming matters as much as the judgement. A round that invoked
nobody, one whose candidate never started, and one whose candidate ran
without finishing are three different facts; all three used to reach the
operator as "the resolver declined the work", and the durable evidence
string that outlives the run said so too.

Split out of :mod:`ralph.pipeline.conflict_resolution.driver`, which
still owns the loop these verdicts steer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.attempt_fault import (
    INFRASTRUCTURE_TERMINATION_REASONS,
    RESOLVER_NOT_SPENT_TERMINATION_REASONS,
)
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.conflict_resolution.session import ResolutionSession

__all__ = [
    "is_effectively_empty",
    "reset_round_reporting",
    "resolution_exhaustion_reason",
    "resolution_outcome",
    "round_termination_reason",
]


def resolution_outcome(session: ResolutionSession, succeeded: bool) -> ResolutionOutcome:
    """Expose one resolver invocation as typed outcome evidence."""
    cause = ResolutionFailureCause.NONE
    if not succeeded:
        if session.last_failure_cause != ResolutionFailureCause.NONE:
            cause = session.last_failure_cause
        elif session.terminal_reason in RESOLVER_NOT_SPENT_TERMINATION_REASONS:
            cause = ResolutionFailureCause.AGENT_FAILURE
        elif session.terminal_reason is not None:
            cause = ResolutionFailureCause.UNRESOLVED_CONFLICT
    ex_msg = (
        session.operator_action_message
        if not succeeded and session.operator_action_message
        else (session.exhaustion_reason if not succeeded else None)
    )
    return ResolutionOutcome(
        succeeded=succeeded,
        reason=None if succeeded else session.terminal_reason,
        duration_seconds=session.last_duration_seconds or 0.0,
        last_activity_kind=session.last_activity_kind,
        last_activity_at=session.last_activity_at,
        unresolved_paths=session.unresolved_paths,
        failure_cause=cause,
        exhaustion_message=ex_msg,
    )


def reset_round_reporting(session: ResolutionSession) -> None:
    """Clear per-round reporting state before a fresh round runs."""
    if (
        session.terminal_reason not in INFRASTRUCTURE_TERMINATION_REASONS
        and session.terminal_reason is not ResolutionTerminationReason.EXCEPTION
    ):
        session.terminal_reason = None
    session.last_activity_kind = None
    session.last_activity_at = None
    session.last_duration_seconds = None


def is_effectively_empty(path: Path) -> bool:
    """Whether a file holds nothing a resolution could have meant to keep."""
    try:
        return not path.read_bytes().strip()
    except OSError:
        return False


def _uninvoked_round_reason(
    session: ResolutionSession,
    candidates: Sequence[str],
) -> ResolutionTerminationReason:
    effective_candidates = list(candidates)
    if not effective_candidates and session.recovery_state is not None:
        state = session.recovery_state
        from ralph.pipeline.state import PipelineState

        if isinstance(state, PipelineState):
            ch = state.chain_for_phase(PHASE_RESOLUTION)
            if ch is not None and ch.agents:
                effective_candidates = list(ch.agents)

    controller = session.recovery_controller
    if controller is not None and effective_candidates:
        delay = controller.earliest_available_wait_ms(PHASE_RESOLUTION, effective_candidates)
        if delay > 0:
            return ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
        if controller.all_exhausted(PHASE_RESOLUTION, effective_candidates):
            return ResolutionTerminationReason.CANDIDATE_EXHAUSTED
    ledger = session.ledger
    if ledger is not None and effective_candidates:
        wait = ledger.earliest_wait_ms(PHASE_RESOLUTION, effective_candidates)
        if wait is not None and wait > 0:
            return ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
        if wait is None:
            return ResolutionTerminationReason.CANDIDATE_EXHAUSTED

    return ResolutionTerminationReason.CANDIDATE_EXHAUSTED


def round_termination_reason(
    session: ResolutionSession,
    *,
    invoked: bool,
    candidates: Sequence[str] = (),
) -> ResolutionTerminationReason:
    """Name what ended the round without inventing a verdict nobody gave.

    No reason here is the agent's opinion of the conflict -- it has no
    way to give one. A round that invoked nobody, one whose candidate
    never started, and one whose candidate ran without finishing are
    three different facts, and all three used to be reported as the
    resolver declining the work.
    """
    launch_attempted = session.rounds_spent > 0 or session.last_attempt_saw_activity
    if session.terminal_reason is not None:
        if (
            session.terminal_reason is ResolutionTerminationReason.TOOL_SURFACE_DEAD
            and not launch_attempted
        ):
            # Dead tool surface is only reported for an observed dead launch.
            pass
        else:
            return session.terminal_reason

    if not invoked:
        return _uninvoked_round_reason(session, candidates)

    # The invocation came back successful and the markers are still
    # there: the resolver worked and did not finish. That is unfinished
    # work, not an answer, and the next round hands it back the paths
    # that still carry markers.
    return ResolutionTerminationReason.RESOLUTION_INCOMPLETE


def resolution_exhaustion_reason(
    session: ResolutionSession,
    unresolved_paths: tuple[str, ...],
) -> str:
    """Build durable terminal evidence when the resolver cannot finish."""
    if (
        session.terminal_reason == ResolutionTerminationReason.OUT_OF_REACH
        and session.operator_action_message
        and "needs operator action:" in session.operator_action_message
    ):
        return session.operator_action_message
    if (
        session.terminal_reason == ResolutionTerminationReason.OUT_OF_REACH
        and session.exhaustion_reason
        and "needs operator action:" in session.exhaustion_reason
    ):
        return session.exhaustion_reason
    if session.terminal_reason == ResolutionTerminationReason.OUT_OF_REACH and unresolved_paths:
        from ralph.pipeline.conflict_resolution.sight import gitlink_escalation_message

        return gitlink_escalation_message(unresolved_paths)
    reason = (
        session.terminal_reason.value
        if session.terminal_reason is not None
        else "RESOLUTION_CHAIN_EXHAUSTED"
    )
    paths = ", ".join(unresolved_paths) or "none still carrying markers"
    # "Conflict markers survive in ..." was asserted for every reason,
    # including exits that never ran a marker scan and paths that cannot
    # carry markers at all -- a binary file, a modify/delete. Say the
    # thing that is true of all of them.
    return f"{reason}: unresolved paths: {paths}"
