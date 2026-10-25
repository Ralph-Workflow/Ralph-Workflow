"""The typed result of one conflict-resolution round.

A round spends zero or more resolution candidates, and what it produced
is one record: whether a candidate ran, whether it succeeded, and which
agent was responsible. This module owns that record and the two ways a
raw attempt becomes typed evidence -- the invoker's own outcome folded
into the session, and the verdict the driver acts on (proceed, accept
the worktree repair, or escalate).

The driver that spends the rounds stays in
:mod:`ralph.pipeline.conflict_resolution.driver`; this module holds the
RESULT of a round, not the round loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.pipeline.conflict_resolution._driver_events import emit_attempt_outcome
from ralph.pipeline.conflict_resolution.attempt_fault import (
    RESOLVER_NOT_SPENT_TERMINATION_REASONS,
)
from ralph.pipeline.conflict_resolution.resolution_action import (
    select_resolution_action,
)
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
from ralph.pipeline.conflict_resolution.round_verdict import round_termination_reason
from ralph.pipeline.rebase_state import ResolutionFailureCause

if TYPE_CHECKING:
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.conflict_resolution.operator_cap import MonotonicClock
    from ralph.pipeline.conflict_resolution.resolution_action import ResolutionAction
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

__all__ = ["RoundAttempt"]


@dataclass(frozen=True)
class RoundAttempt:
    """Record success and whether a resolution candidate was invoked."""

    succeeded: bool
    invoked: bool
    agent_ran: bool = False
    agent_name: str | None = None
    candidate_offset: int = 0


def _record_invoker_outcome(
    session: ResolutionSession,
    invoker_result: ResolutionOutcome,
) -> None:
    """Fold one invoker's typed outcome into the resolution session.

    The invoker reports what happened to ITS candidate; the session is
    the durable record the next round and the next seam read. Every
    field the invoker observed is copied over verbatim, and an agent
    failure clears the conflict budget so an infrastructure fault is
    never charged to the resolver chain.
    """
    session.last_failure_cause = invoker_result.failure_cause
    if invoker_result.reason is not None:
        session.terminal_reason = invoker_result.reason
    if invoker_result.last_activity_kind is not None:
        session.last_activity_kind = invoker_result.last_activity_kind
    if invoker_result.last_activity_at is not None:
        session.last_activity_at = invoker_result.last_activity_at
    if invoker_result.duration_seconds > 0:
        session.last_duration_seconds = invoker_result.duration_seconds
    if invoker_result.unresolved_paths:
        session.unresolved_paths = invoker_result.unresolved_paths
    if invoker_result.failure_cause == ResolutionFailureCause.AGENT_FAILURE:
        session.charge_conflict_budget = False


def _evaluate_round_attempt(
    attempt: RoundAttempt,
    session: ResolutionSession,
    display: ParallelDisplay | None,
    round_index: int,
    round_cap: int,
    attempt_started: float,
    clock: MonotonicClock,
    decision_paths: tuple[str, ...],
) -> ResolutionAction:
    """Return the action the driver takes after one round attempt.

    A round that succeeded, or whose worktree proves every marker
    repaired, resolves the stop -- unless the session still carries
    unresolved paths. The verdict carries the typed terminal reason, the
    duration the round took, and the failure cause the next seam reads.
    """
    succeeded = attempt.succeeded
    agent_ran = attempt.invoked and attempt.agent_ran
    proved_by_worktree = agent_ran and not decision_paths
    resolved_now = (succeeded or proved_by_worktree) and not session.unresolved_paths
    action = select_resolution_action(session, round_index, round_cap, resolved=resolved_now)
    round_reason = (
        None if resolved_now else round_termination_reason(session, invoked=attempt.invoked)
    )
    session.terminal_reason = round_reason
    outcome = ResolutionOutcome(
        succeeded=resolved_now,
        reason=round_reason,
        duration_seconds=(
            session.last_duration_seconds
            if session.last_duration_seconds is not None
            else max(0.0, clock() - attempt_started)
        ),
        last_activity_kind=session.last_activity_kind,
        last_activity_at=session.last_activity_at,
        unresolved_paths=session.unresolved_paths,
        failure_cause=(
            ResolutionFailureCause.NONE
            if resolved_now
            else (
                session.last_failure_cause
                if session.last_failure_cause != ResolutionFailureCause.NONE
                else (
                    ResolutionFailureCause.AGENT_FAILURE
                    if round_reason in RESOLVER_NOT_SPENT_TERMINATION_REASONS
                    else ResolutionFailureCause.UNRESOLVED_CONFLICT
                )
            )
        ),
    )
    emit_attempt_outcome(display, outcome)
    return action
