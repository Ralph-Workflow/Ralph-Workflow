"""Unavailability reason taxonomy and per-reason backoff policy defaults.

This module defines the closed set of reasons an agent can be marked unavailable,
and the default backoff policy for each reason.

Design rationale for retiring RUNNING_SUBAGENTS_QUIET:
  The prior taxonomy included a RUNNING_SUBAGENTS_QUIET reason mapped from
  no_progress_quiet / children_persist_too_long watchdog fires. This was
  incorrect because the repo has no positive signal source for productive
  subagent running — mapping a negative watchdog fire (stale/no-progress
  evidence) to a "healthy running subagents" label would mislabel a stuck
  agent as healthy. The correct mapping is:
    - watchdog_reason == "no_progress_quiet" -> STALE_CHILD_QUIET
      (child alive with stale-progress evidence: heartbeat-only, stale-label,
      or OS-descendant-only — a NEGATIVE signal)
    - watchdog_reason == "children_persist_too_long" -> SUSPICIOUS_TIMEOUT_NO_OUTPUT
      (cumulative waiting ceiling hit; also a NEGATIVE/stuck signal)
  The operator-visible output now distinguishes: out_of_credits vs
  no_output_at_start vs stale_child vs suspicious_timeout.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "DEFAULT_UNAVAILABILITY_BACKOFF_POLICY",
    "ReasonBackoffPolicy",
    "UnavailabilityReason",
]


class UnavailabilityReason(StrEnum):
    """Why an agent is temporarily unavailable."""

    OUT_OF_CREDITS = "out_of_credits"
    NO_OUTPUT_AT_START = "no_output_at_start"
    NO_OUTPUT_AFTER_ACTIVITY = "no_output_after_activity"
    SUSPICIOUS_TIMEOUT_NO_OUTPUT = "suspicious_timeout_no_output"
    STALE_CHILD_QUIET = "stale_child_quiet"
    # Stuck-but-alive job: orthogonal to STALE_CHILD_QUIET (which is for
    # dead children). Fires when the corroborator reports a live child
    # with no recent progress / heartbeat for the
    # ``no_progress_quiet_strictly_stuck_seconds`` ceiling. Same
    # backoff policy as STALE_CHILD_QUIET (15 s base, 300 s max).
    STRICTLY_STUCK = "strictly_stuck"


@dataclass(frozen=True)
class ReasonBackoffPolicy:
    """Per-reason exponential backoff parameters."""

    base_backoff_ms: int
    max_backoff_ms: int

    def __post_init__(self) -> None:
        if self.base_backoff_ms <= 0:
            msg = "base_backoff_ms must be positive"
            raise ValueError(msg)
        if self.max_backoff_ms <= self.base_backoff_ms:
            msg = "max_backoff_ms must be strictly greater than base_backoff_ms"
            raise ValueError(msg)


DEFAULT_UNAVAILABILITY_BACKOFF_POLICY = {  # bounded-accumulator-ok: static
    UnavailabilityReason.OUT_OF_CREDITS: ReasonBackoffPolicy(
        base_backoff_ms=60_000,
        max_backoff_ms=1_800_000,
    ),
    UnavailabilityReason.NO_OUTPUT_AT_START: ReasonBackoffPolicy(
        base_backoff_ms=5_000,
        max_backoff_ms=30_000,
    ),
    UnavailabilityReason.NO_OUTPUT_AFTER_ACTIVITY: ReasonBackoffPolicy(
        base_backoff_ms=10_000,
        max_backoff_ms=120_000,
    ),
    UnavailabilityReason.SUSPICIOUS_TIMEOUT_NO_OUTPUT: ReasonBackoffPolicy(
        base_backoff_ms=10_000,
        max_backoff_ms=60_000,
    ),
    UnavailabilityReason.STALE_CHILD_QUIET: ReasonBackoffPolicy(
        base_backoff_ms=15_000,
        max_backoff_ms=300_000,
    ),
    UnavailabilityReason.STRICTLY_STUCK: ReasonBackoffPolicy(
        base_backoff_ms=15_000,
        max_backoff_ms=300_000,
    ),
}
