"""Typed terminal reasons for a conflict-resolution attempt."""

from __future__ import annotations

from enum import StrEnum


class ResolutionTerminationReason(StrEnum):
    """Reason a resolver returned control to its conflict owner.

    None of these is an agent refusing the work, because no agent can:
    the resolution session's only completion signal is
    ``declare_complete``, and there is no tool with which it could say
    "I will not do this". A reason named for a refusal therefore
    described something that cannot happen while hiding what did --
    an invocation that failed (``ATTEMPT_FAILED``), one that never
    started (``CANDIDATE_UNAVAILABLE``, ``CANDIDATE_EXITED``), or work
    that ran and did not finish (``RESOLUTION_INCOMPLETE``). Each of
    those is answered by trying again, not by accepting a verdict.
    """

    CONFLICT_INACTIVITY = "CONFLICT_INACTIVITY"
    OPERATOR_CAP_REACHED = "OPERATOR_CAP_REACHED"
    SUPERVISION_INFRASTRUCTURE_FAILURE = "SUPERVISION_INFRASTRUCTURE_FAILURE"
    TRANSPORT_LOOP_DETECTED = "TRANSPORT_LOOP_DETECTED"
    TOOL_SURFACE_DEAD = "TOOL_SURFACE_DEAD"
    OUT_OF_REACH = "OUT_OF_REACH"
    ATTEMPT_FAILED = "ATTEMPT_FAILED"
    RESOLUTION_INCOMPLETE = "RESOLUTION_INCOMPLETE"
    CANDIDATE_EXITED = "CANDIDATE_EXITED"
    CANDIDATE_UNAVAILABLE = "CANDIDATE_UNAVAILABLE"
    EXCEPTION = "EXCEPTION"


__all__ = ["ResolutionTerminationReason"]
