from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.attempt_fault import (
    BUDGET_UNCHARGED_TERMINATION_REASONS,
    INFRASTRUCTURE_TERMINATION_REASONS,
)
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.resolution_outcome import (
    ResolvedOutcome,
    StopResolutionOutcome,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.conflict_resolution.session import ResolutionSession
    from ralph.pipeline.resolution_outcome import StopResolutionOutcome

__all__ = [
    "is_effectively_empty",
    "reset_round_reporting",
    "resolution_halt_reason",
    "resolution_outcome",
    "round_termination_reason",
]


def resolution_outcome(session: ResolutionSession, succeeded: bool) -> StopResolutionOutcome:
    from ralph.pipeline.resolution_outcome_producer import (
        classify_resolution_failure,
    )

    if succeeded:
        return ResolvedOutcome()
    cause = ResolutionFailureCause.NONE
    if not succeeded:
        if session.last_failure_cause != ResolutionFailureCause.NONE:
            cause = session.last_failure_cause
        elif session.terminal_reason in BUDGET_UNCHARGED_TERMINATION_REASONS:
            cause = ResolutionFailureCause.AGENT_FAILURE
        elif session.terminal_reason is not None:
            cause = ResolutionFailureCause.UNRESOLVED_CONFLICT
    ex_msg = (
        session.operator_action_message if session.operator_action_message else session.halt_reason
    )
    return classify_resolution_failure(
        terminal_reason=session.terminal_reason,
        failure_cause=cause,
        reason=ex_msg,
        paths=session.unresolved_paths,
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
    if controller is not None:
        target = effective_candidates if effective_candidates else tuple(candidates)
        if target:
            delay = controller.earliest_available_wait_ms(PHASE_RESOLUTION, target)
            if delay > 0:
                return ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    ledger = session.ledger
    if ledger is not None:
        target = effective_candidates if effective_candidates else tuple(candidates)
        if target and ledger.earliest_wait_ms(PHASE_RESOLUTION, target) > 0:
            return ResolutionTerminationReason.CANDIDATE_UNAVAILABLE

    return ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE


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


def resolution_halt_reason(
    session: ResolutionSession,
    unresolved_paths: tuple[str, ...],
) -> str:
    action_msg = (
        session.operator_action_message
        if session.operator_action_message and "resume point:" in session.operator_action_message
        else (
            session.halt_reason
            if session.halt_reason and "resume point:" in session.halt_reason
            else None
        )
    )
    if action_msg:
        if session.terminal_reason == ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE:
            prefix = f"{ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE.value}: "
            return action_msg if action_msg.startswith(prefix) else f"{prefix}{action_msg}"
        return action_msg
    if session.terminal_reason == ResolutionTerminationReason.OUT_OF_REACH and unresolved_paths:
        from ralph.pipeline.conflict_resolution.sight import gitlink_escalation_message

        return gitlink_escalation_message(unresolved_paths)
    if (
        session.terminal_reason == ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE
        and unresolved_paths
    ):
        from ralph.pipeline.conflict_resolution._operator_command import (
            operator_command_for_paths,
        )
        from ralph.pipeline.conflict_resolution.resolver_cooldown_text import (
            resolvers_on_cooldown_reason,
        )

        cmd = operator_command_for_paths(unresolved_paths)
        return (
            f"{ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE.value}: "
            f"{resolvers_on_cooldown_reason(unresolved_paths, cmd)}"
        )
    reason = (
        session.terminal_reason
        if session.terminal_reason is not None
        else ResolutionTerminationReason.RESOLUTION_INCOMPLETE
    ).value
    paths = ", ".join(unresolved_paths) or "none still carrying markers"
    return f"{reason}: unresolved paths: {paths}"
