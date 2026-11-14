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
from ralph.pipeline.resolution_outcome import (
    ResolvedOutcome,
    StopResolutionOutcome,
)

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


def resolution_outcome(session: ResolutionSession, succeeded: bool) -> StopResolutionOutcome:
    from dataclasses import replace

    from ralph.pipeline.resolution_outcome_producer import (
        classify_resolution_failure,
    )

    unresolved = tuple(session.unresolved_paths or ())
    if succeeded:
        return ResolvedOutcome(
            succeeded=True,
            reason=None,
            unresolved_paths=unresolved,
        )
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
    raw_outcome = classify_resolution_failure(
        terminal_reason=session.terminal_reason,
        failure_cause=cause,
        reason=ex_msg,
        paths=session.unresolved_paths,
    )
    outcome = raw_outcome
    return replace(
        outcome,
        succeeded=False,
        reason=session.terminal_reason,
        unresolved_paths=unresolved,
    )


def reset_round_reporting(session: ResolutionSession) -> None:
    if (
        session.terminal_reason not in INFRASTRUCTURE_TERMINATION_REASONS
        and session.terminal_reason is not ResolutionTerminationReason.EXCEPTION
    ):
        session.terminal_reason = None
    session.last_activity_kind = None
    session.last_activity_at = None
    session.last_duration_seconds = None


def is_effectively_empty(path: Path) -> bool:
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
    launch_attempted = session.rounds_spent > 0 or session.last_attempt_saw_activity
    if session.terminal_reason is not None:
        if (
            session.terminal_reason is ResolutionTerminationReason.TOOL_SURFACE_DEAD
            and not launch_attempted
        ):
            pass
        else:
            return session.terminal_reason

    if not invoked:
        return _uninvoked_round_reason(session, candidates)

    return ResolutionTerminationReason.RESOLUTION_INCOMPLETE


def resolution_exhaustion_reason(
    session: ResolutionSession,
    unresolved_paths: tuple[str, ...],
) -> str:
    action_msg = (
        session.operator_action_message
        if session.operator_action_message
        and "needs operator action:" in session.operator_action_message
        else (
            session.exhaustion_reason
            if session.exhaustion_reason and "needs operator action:" in session.exhaustion_reason
            else None
        )
    )
    if action_msg:
        if session.terminal_reason == ResolutionTerminationReason.CANDIDATE_EXHAUSTED:
            prefix = f"{ResolutionTerminationReason.CANDIDATE_EXHAUSTED.value}: "
            return action_msg if action_msg.startswith(prefix) else f"{prefix}{action_msg}"
        return action_msg
    if session.terminal_reason == ResolutionTerminationReason.OUT_OF_REACH and unresolved_paths:
        from ralph.pipeline.conflict_resolution.sight import gitlink_escalation_message

        return gitlink_escalation_message(unresolved_paths)
    if (
        session.terminal_reason == ResolutionTerminationReason.CANDIDATE_EXHAUSTED
        and unresolved_paths
    ):
        from ralph.pipeline.conflict_resolution._operator_command import (
            operator_command_for_paths,
        )

        paths = ", ".join(unresolved_paths)
        cmd = operator_command_for_paths(unresolved_paths)
        return (
            f"CANDIDATE_EXHAUSTED: all resolver agents are spent/unavailable; "
            f"conflict on {paths} needs operator action: {cmd}"
        )
    reason = (
        session.terminal_reason.value
        if session.terminal_reason is not None
        else "RESOLUTION_CHAIN_EXHAUSTED"
    )
    paths = ", ".join(unresolved_paths) or "none still carrying markers"
    return f"{reason}: unresolved paths: {paths}"
