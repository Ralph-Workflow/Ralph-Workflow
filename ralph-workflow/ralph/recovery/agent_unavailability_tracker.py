"""Agent unavailability tracker with per-reason exponential backoff.

Sole owner of unavailable storage. RecoveryController delegates to this class
instead of directly managing _unavailable_timeouts and _backoff_attempts dicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.agents.timeout_clock import SystemClock
from ralph.recovery.unavailability_reason import (
    DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    ReasonBackoffPolicy,
    UnavailabilityReason,
)

if TYPE_CHECKING:
    from ralph.agents.timeout_clock import Clock


@dataclass(frozen=True)
class UnavailabilityEntry:
    """An agent's unavailable entry with backoff state."""

    unavailable_until_ms: int
    reason: UnavailabilityReason | None
    attempt: int
    base_backoff_ms: int
    max_backoff_ms: int


DEFAULT_LEGACY_BACKOFF_MS = 5_000
DEFAULT_LEGACY_MAX_BACKOFF_MS = 300_000


class AgentUnavailabilityTracker:
    """Tracks agent unavailability with per-reason exponential backoff.

    Sole owner of unavailable storage. The RecoveryController delegates to
    this class rather than managing _unavailable_timeouts and _backoff_attempts
    directly.

    Args:
        clock: Clock for time-dependent decisions. Defaults to system clock.
        backoff_policy: Per-reason backoff policy mapping. Defaults to
            DEFAULT_UNAVAILABILITY_BACKOFF_POLICY.
        initial_entries: Optional pre-seeded entries (for testing).
        initial_timeouts: Legacy seam — optional pre-seeded timeouts dict
            (for backward compatibility with tests that use the old
            unavailable_timeouts dict).
    """

    def __init__(
        self,
        clock: Clock | None = None,
        backoff_policy: dict[UnavailabilityReason, ReasonBackoffPolicy] | None = None,
        initial_entries: dict[str, UnavailabilityEntry] | None = None,
        initial_timeouts: dict[str, int] | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._backoff_policy: dict[UnavailabilityReason, ReasonBackoffPolicy] = (
            backoff_policy if backoff_policy is not None
            else DEFAULT_UNAVAILABILITY_BACKOFF_POLICY
        )
        self._entries: dict[str, UnavailabilityEntry] = dict(initial_entries or {})
        self._backoff_attempts: dict[str, int] = {}

        if initial_timeouts:
            for key, timeout_ms in initial_timeouts.items():
                self._entries[key] = UnavailabilityEntry(
                    unavailable_until_ms=timeout_ms,
                    reason=None,
                    attempt=0,
                    base_backoff_ms=DEFAULT_LEGACY_BACKOFF_MS,
                    max_backoff_ms=DEFAULT_LEGACY_MAX_BACKOFF_MS,
                )

    def mark_unavailable(
        self,
        phase: str,
        agent: str,
        reason: UnavailabilityReason | None = None,
    ) -> UnavailabilityEntry:
        """Mark an agent unavailable with per-reason exponential backoff.

        Args:
            phase: Pipeline phase.
            agent: Agent name.
            reason: The unavailability reason (determines backoff policy).

        Returns:
            The new UnavailabilityEntry with computed backoff.
        """
        key = f"{phase}:{agent}"
        current_time_ms = int(self._clock.monotonic() * 1000)
        attempt: int = self._backoff_attempts.get(key, 0)

        if reason is not None and reason in self._backoff_policy:
            policy = self._backoff_policy[reason]
        else:
            policy = None

        if policy is not None:
            base_ms = policy.base_backoff_ms
            cap_ms = policy.max_backoff_ms
        else:
            base_ms = DEFAULT_LEGACY_BACKOFF_MS
            cap_ms = DEFAULT_LEGACY_MAX_BACKOFF_MS

        base_ms_int: int = int(base_ms)
        cap_ms_int: int = int(cap_ms)

        multiplier: int = pow(2, attempt)
        backoff_ms: int = base_ms_int * multiplier
        if backoff_ms > cap_ms_int:
            backoff_ms = cap_ms_int

        unavailable_until_ms = current_time_ms + backoff_ms
        self._entries[key] = UnavailabilityEntry(
            unavailable_until_ms=unavailable_until_ms,
            reason=reason,
            attempt=attempt,
            base_backoff_ms=base_ms_int,
            max_backoff_ms=cap_ms_int,
        )
        self._backoff_attempts[key] = attempt + 1
        return self._entries[key]

    def is_available(self, phase: str, agent: str) -> bool:
        """Return True when the agent is not currently marked unavailable."""
        key = f"{phase}:{agent}"
        entry = self._entries.get(key)
        if entry is None:
            return True
        current_time_ms = int(self._clock.monotonic() * 1000)
        return current_time_ms >= entry.unavailable_until_ms

    def earliest_unavailable_wait_ms(self, phase: str, agents: list[str]) -> int:
        """Return milliseconds until the earliest unavailable agent becomes available.

        Returns 0 if any agent is available.
        """
        current_time_ms = int(self._clock.monotonic() * 1000)
        min_remaining: int | None = None
        for agent in agents:
            key = f"{phase}:{agent}"
            entry = self._entries.get(key)
            if entry is None:
                return 0
            if entry.unavailable_until_ms > current_time_ms:
                remaining = entry.unavailable_until_ms - current_time_ms
                if min_remaining is None or remaining < min_remaining:
                    min_remaining = remaining
        return max(0, min_remaining or 0)

    def reset_backoff(self, phase: str, agent: str) -> None:
        """Clear the unavailable entry for a phase:agent."""
        key = f"{phase}:{agent}"
        self._entries.pop(key, None)
        self._backoff_attempts.pop(key, None)

    def snapshot(self) -> dict[str, dict[str, object]]:
        """Return a defensive copy of the internal state."""
        return {
            "unavailable_timeouts": {
                key: entry.unavailable_until_ms
                for key, entry in self._entries.items()
            },
            "backoff_attempts": dict(self._backoff_attempts),
        }


__all__ = ["AgentUnavailabilityTracker", "UnavailabilityEntry"]
