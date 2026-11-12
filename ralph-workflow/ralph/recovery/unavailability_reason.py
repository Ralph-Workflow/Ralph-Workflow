from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class UnavailabilityReason(StrEnum):
    OUT_OF_CREDITS = "out_of_credits"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    CONTEXT_EXHAUSTED = "context_exhausted"
    PROCESS_EXITED = "process_exited"
    AUTH_CONFIG = "auth_config"
    BROKEN_AGENT = "broken_agent"
    NO_OUTPUT_AT_START = "no_output_at_start"
    NO_OUTPUT_AFTER_ACTIVITY = "no_output_after_activity"
    SUSPICIOUS_TIMEOUT_NO_OUTPUT = "suspicious_timeout_no_output"
    STALE_CHILD_QUIET = "stale_child_quiet"
    STRICTLY_STUCK = "strictly_stuck"
    DEAD_AGENT_NO_PROGRESS = "dead_agent_no_progress"


@dataclass(frozen=True)
class ReasonBackoffPolicy:
    base_backoff_ms: int
    max_backoff_ms: int

    def __post_init__(self) -> None:
        if self.base_backoff_ms <= 0:
            msg = "base_backoff_ms must be positive"
            raise ValueError(msg)
        if self.max_backoff_ms <= self.base_backoff_ms:
            msg = "max_backoff_ms must be strictly greater than base_backoff_ms"
            raise ValueError(msg)


_UNIVERSAL_COOLDOWN_CAP_MS: int = 18_000_000

DEFAULT_UNAVAILABILITY_BACKOFF_POLICY = {
    UnavailabilityReason.OUT_OF_CREDITS: ReasonBackoffPolicy(
        base_backoff_ms=60_000,
        max_backoff_ms=_UNIVERSAL_COOLDOWN_CAP_MS,
    ),
    UnavailabilityReason.PROVIDER_UNAVAILABLE: ReasonBackoffPolicy(
        base_backoff_ms=60_000,
        max_backoff_ms=_UNIVERSAL_COOLDOWN_CAP_MS,
    ),
    UnavailabilityReason.CONTEXT_EXHAUSTED: ReasonBackoffPolicy(
        base_backoff_ms=5_000,
        max_backoff_ms=_UNIVERSAL_COOLDOWN_CAP_MS,
    ),
    UnavailabilityReason.PROCESS_EXITED: ReasonBackoffPolicy(
        base_backoff_ms=5_000,
        max_backoff_ms=_UNIVERSAL_COOLDOWN_CAP_MS,
    ),
    UnavailabilityReason.AUTH_CONFIG: ReasonBackoffPolicy(
        base_backoff_ms=5_000,
        max_backoff_ms=_UNIVERSAL_COOLDOWN_CAP_MS,
    ),
    UnavailabilityReason.BROKEN_AGENT: ReasonBackoffPolicy(
        base_backoff_ms=5_000,
        max_backoff_ms=_UNIVERSAL_COOLDOWN_CAP_MS,
    ),
    UnavailabilityReason.NO_OUTPUT_AT_START: ReasonBackoffPolicy(
        base_backoff_ms=5_000,
        max_backoff_ms=_UNIVERSAL_COOLDOWN_CAP_MS,
    ),
    UnavailabilityReason.NO_OUTPUT_AFTER_ACTIVITY: ReasonBackoffPolicy(
        base_backoff_ms=10_000,
        max_backoff_ms=_UNIVERSAL_COOLDOWN_CAP_MS,
    ),
    UnavailabilityReason.SUSPICIOUS_TIMEOUT_NO_OUTPUT: ReasonBackoffPolicy(
        base_backoff_ms=10_000,
        max_backoff_ms=_UNIVERSAL_COOLDOWN_CAP_MS,
    ),
    UnavailabilityReason.STALE_CHILD_QUIET: ReasonBackoffPolicy(
        base_backoff_ms=15_000,
        max_backoff_ms=_UNIVERSAL_COOLDOWN_CAP_MS,
    ),
    UnavailabilityReason.STRICTLY_STUCK: ReasonBackoffPolicy(
        base_backoff_ms=15_000,
        max_backoff_ms=_UNIVERSAL_COOLDOWN_CAP_MS,
    ),
    UnavailabilityReason.DEAD_AGENT_NO_PROGRESS: ReasonBackoffPolicy(
        base_backoff_ms=10_000,
        max_backoff_ms=_UNIVERSAL_COOLDOWN_CAP_MS,
    ),
}

__all__ = [
    "DEFAULT_UNAVAILABILITY_BACKOFF_POLICY",
    "_UNIVERSAL_COOLDOWN_CAP_MS",
    "ReasonBackoffPolicy",
    "UnavailabilityReason",
]
