"""Agent failure ledger: single-owner component for agent failure and demotion decisions.

This component tracks consecutive and total unproductive execution attempts per
(phase, agent) pair and decides whether an agent remains eligible, enters cooldown,
or becomes permanently spent.

All decisions are pure functions of observed facts and injected time; no ambient
system reads (clock, filesystem, process table) are performed.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.clock import Clock


@dataclass(frozen=True)
class _AttemptFact:
    """Observed facts about an execution attempt."""

    phase: str
    agent: str
    artifact_valid: bool
    workspace_changed: bool = False
    free_retry_category: bool = False


class _AgentStatus:
    """Base class for agent availability status."""

    @property
    def is_eligible(self) -> bool:
        """Return True if the agent is eligible to execute."""
        return isinstance(self, _Eligible)

    @property
    def is_cooldown(self) -> bool:
        """Return True if the agent is in cooldown."""
        return isinstance(self, _Cooldown)

    @property
    def is_spent(self) -> bool:
        """Return True if the agent is demoted/spent."""
        return isinstance(self, _Spent)


@dataclass(frozen=True)
class _Eligible(_AgentStatus):
    """Agent is eligible to run."""


@dataclass(frozen=True)
class _Cooldown(_AgentStatus):
    """Agent is temporarily cooling down until monotonic timestamp `until_ms`."""

    until_ms: int
    reason: str


@dataclass(frozen=True)
class _Spent(_AgentStatus):
    """Agent is demoted/spent for the rest of the phase or run."""

    reason: str


@dataclass(frozen=True)
class _AttemptOutcome:
    """Outcome of recording an attempt fact."""

    status: _AgentStatus
    consecutive_unproductive: int
    total_unproductive: int

    @property
    def demoted(self) -> bool:
        """Return True if the agent is no longer eligible (in cooldown or spent)."""
        return not isinstance(self.status, _Eligible)

    @property
    def reason(self) -> str | None:
        """Return the demotion reason if not eligible, else None."""
        if isinstance(self.status, (_Cooldown, _Spent)):
            return self.status.reason
        return None


AttemptFact = _AttemptFact
AgentStatus = _AgentStatus
Eligible = _Eligible
Cooldown = _Cooldown
Spent = _Spent
AttemptOutcome = _AttemptOutcome


@dataclass
class _LedgerEntry:
    """Internal mutable state for a single (phase, agent) pair."""

    consecutive_unproductive: int = 0
    total_unproductive: int = 0
    cooldown_until_ms: int | None = None
    cooldown_reason: str | None = None
    cooldown_count: int = 0
    is_spent: bool = False
    spent_reason: str | None = None


_COOLDOWN_REASON_CONSECUTIVE = "consecutive_unproductive_limit_reached"
_SPENT_REASON_TOTAL = "total_unproductive_limit_reached"


class AgentFailureLedger:
    """Single owner of agent failure, cooldown, and demotion decisions.

    Tracks consecutive and total unproductive attempts per (phase, agent) and
    determines agent eligibility.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        consecutive_limit: int = 3,
        total_limit: int = 9,
        base_cooldown_seconds: float = 60.0,
        max_cooldown_seconds: float = 3600.0,
        backoff_factor: float = 2.0,
    ) -> None:
        if consecutive_limit <= 0:
            msg = f"consecutive_limit must be positive, got {consecutive_limit}"
            raise ValueError(msg)
        if total_limit <= 0:
            msg = f"total_limit must be positive, got {total_limit}"
            raise ValueError(msg)
        if base_cooldown_seconds <= 0.0:
            msg = f"base_cooldown_seconds must be positive, got {base_cooldown_seconds}"
            raise ValueError(msg)
        if max_cooldown_seconds < base_cooldown_seconds:
            msg = (
                f"max_cooldown_seconds ({max_cooldown_seconds}) must be greater than "
                f"or equal to base_cooldown_seconds ({base_cooldown_seconds})"
            )
            raise ValueError(msg)
        if backoff_factor < 1.0:
            msg = f"backoff_factor must be >= 1.0, got {backoff_factor}"
            raise ValueError(msg)

        self._clock: Clock = clock
        self._consecutive_limit: int = consecutive_limit
        self._total_limit: int = total_limit
        self._base_cooldown_seconds: float = base_cooldown_seconds
        self._max_cooldown_seconds: float = max_cooldown_seconds
        self._backoff_factor: float = backoff_factor
        self._lock = threading.Lock()
        self._entries: dict[
            tuple[str, str], _LedgerEntry
        ] = {}  # bounded-accumulator-ok: per-(phase, agent) failure tracking bounded by agent chain size

    @property
    def consecutive_limit(self) -> int:
        """Configured consecutive unproductive attempt limit."""
        return self._consecutive_limit

    @property
    def total_limit(self) -> int:
        """Configured total unproductive attempt limit."""
        return self._total_limit

    @property
    def base_cooldown_seconds(self) -> float:
        """Configured base cooldown in seconds."""
        return self._base_cooldown_seconds

    @property
    def max_cooldown_seconds(self) -> float:
        """Configured maximum cooldown in seconds."""
        return self._max_cooldown_seconds

    @property
    def backoff_factor(self) -> float:
        """Configured exponential backoff multiplier."""
        return self._backoff_factor

    def _sync_cooldown_expiry(self, entry: _LedgerEntry, now_ms: int) -> None:
        """Check if an active cooldown has elapsed, resetting consecutive count."""
        if entry.is_spent:
            return
        if entry.cooldown_until_ms is not None and now_ms >= entry.cooldown_until_ms:
            entry.cooldown_until_ms = None
            entry.cooldown_reason = None
            entry.consecutive_unproductive = 0

    def _status_locked(self, phase: str, agent: str, now_ms: int) -> AgentStatus:
        """Return status for an agent under lock."""
        entry = self._entries.get((phase, agent))
        if entry is None:
            return Eligible()

        if entry.is_spent:
            return Spent(reason=entry.spent_reason or _SPENT_REASON_TOTAL)

        self._sync_cooldown_expiry(entry, now_ms)

        if entry.cooldown_until_ms is not None:
            return Cooldown(
                until_ms=entry.cooldown_until_ms,
                reason=entry.cooldown_reason or _COOLDOWN_REASON_CONSECUTIVE,
            )
        return Eligible()

    def status(self, phase: str, agent: str) -> AgentStatus:
        """Return the current status of an agent for the specified phase."""
        with self._lock:
            now_ms = int(self._clock.monotonic() * 1000)
            return self._status_locked(phase, agent, now_ms)

    def is_available(self, phase: str, agent: str) -> bool:
        """Return True when the agent is eligible to run."""
        return isinstance(self.status(phase, agent), Eligible)

    def earliest_wait_ms(self, phase: str, agents: list[str]) -> int:
        """Return milliseconds until the earliest cooling-down agent becomes eligible.

        Returns 0 if any agent is currently eligible or if no agents are cooling down.
        """
        with self._lock:
            now_ms = int(self._clock.monotonic() * 1000)
            remaining_times: list[int] = []
            for agent in agents:
                st = self._status_locked(phase, agent, now_ms)
                if isinstance(st, Eligible):
                    return 0
                if isinstance(st, Cooldown):
                    remaining = max(0, st.until_ms - now_ms)
                    remaining_times.append(remaining)
            return min(remaining_times) if remaining_times else 0

    def record(self, fact: AttemptFact) -> AttemptOutcome:
        """Record the observed facts of an attempt and return the outcome."""
        with self._lock:
            key = (fact.phase, fact.agent)
            now_ms = int(self._clock.monotonic() * 1000)

            entry = self._entries.get(key)
            if entry is None:
                entry = _LedgerEntry()
                self._entries[key] = entry

            self._sync_cooldown_expiry(entry, now_ms)

            # Rule: artifact_valid == True clears both consecutive and total counts.
            if fact.artifact_valid:
                entry.consecutive_unproductive = 0
                entry.total_unproductive = 0
                entry.cooldown_until_ms = None
                entry.cooldown_reason = None
                entry.cooldown_count = 0
                entry.is_spent = False
                entry.spent_reason = None
                return AttemptOutcome(
                    status=Eligible(),
                    consecutive_unproductive=0,
                    total_unproductive=0,
                )

            # Unproductive attempt (artifact_valid == False).
            if fact.workspace_changed:
                entry.consecutive_unproductive = 0
                entry.total_unproductive += 1
            else:
                entry.consecutive_unproductive += 1
                entry.total_unproductive += 1

            # Check total limit first: demotes to Spent.
            if entry.total_unproductive >= self._total_limit:
                entry.is_spent = True
                entry.spent_reason = _SPENT_REASON_TOTAL
                entry.cooldown_until_ms = None
                entry.cooldown_reason = None
                return AttemptOutcome(
                    status=Spent(reason=_SPENT_REASON_TOTAL),
                    consecutive_unproductive=entry.consecutive_unproductive,
                    total_unproductive=entry.total_unproductive,
                )

            # Check consecutive limit: puts agent on Cooldown with growing backoff.
            if entry.consecutive_unproductive >= self._consecutive_limit:
                exponent = min(entry.cooldown_count, 30)
                duration_seconds = min(
                    self._base_cooldown_seconds * (self._backoff_factor**exponent),
                    self._max_cooldown_seconds,
                )
                duration_ms = int(duration_seconds * 1000)
                until_ms = now_ms + duration_ms
                entry.cooldown_until_ms = until_ms
                entry.cooldown_reason = _COOLDOWN_REASON_CONSECUTIVE
                entry.cooldown_count += 1
                return AttemptOutcome(
                    status=Cooldown(
                        until_ms=until_ms,
                        reason=_COOLDOWN_REASON_CONSECUTIVE,
                    ),
                    consecutive_unproductive=entry.consecutive_unproductive,
                    total_unproductive=entry.total_unproductive,
                )

            return AttemptOutcome(
                status=Eligible(),
                consecutive_unproductive=entry.consecutive_unproductive,
                total_unproductive=entry.total_unproductive,
            )

    def snapshot(self) -> dict[str, object]:
        """Return a serializable dictionary snapshot of ledger state."""
        with self._lock:
            entries_dict: dict[str, object] = {
                f"{phase}:{agent}": {
                    "phase": phase,
                    "agent": agent,
                    "consecutive_unproductive": entry.consecutive_unproductive,
                    "total_unproductive": entry.total_unproductive,
                    "cooldown_until_ms": entry.cooldown_until_ms,
                    "cooldown_reason": entry.cooldown_reason,
                    "cooldown_count": entry.cooldown_count,
                    "is_spent": entry.is_spent,
                    "spent_reason": entry.spent_reason,
                }
                for (phase, agent), entry in self._entries.items()
            }
            return {
                "consecutive_limit": self._consecutive_limit,
                "total_limit": self._total_limit,
                "base_cooldown_seconds": self._base_cooldown_seconds,
                "max_cooldown_seconds": self._max_cooldown_seconds,
                "backoff_factor": self._backoff_factor,
                "entries": entries_dict,
            }

    @classmethod
    def from_snapshot(
        cls,
        snap: Mapping[str, object],
        *,
        clock: Clock,
    ) -> AgentFailureLedger:
        """Reconstruct an AgentFailureLedger from a snapshot dictionary."""
        consecutive_limit = (
            int(snap["consecutive_limit"])
            if "consecutive_limit" in snap
            and isinstance(snap["consecutive_limit"], (int, float, str))
            else 3
        )
        total_limit = (
            int(snap["total_limit"])
            if "total_limit" in snap and isinstance(snap["total_limit"], (int, float, str))
            else 9
        )
        base_cooldown_seconds = (
            float(snap["base_cooldown_seconds"])
            if "base_cooldown_seconds" in snap
            and isinstance(snap["base_cooldown_seconds"], (int, float, str))
            else 60.0
        )
        max_cooldown_seconds = (
            float(snap["max_cooldown_seconds"])
            if "max_cooldown_seconds" in snap
            and isinstance(snap["max_cooldown_seconds"], (int, float, str))
            else 3600.0
        )
        backoff_factor = (
            float(snap["backoff_factor"])
            if "backoff_factor" in snap and isinstance(snap["backoff_factor"], (int, float, str))
            else 2.0
        )

        ledger = cls(
            clock=clock,
            consecutive_limit=consecutive_limit,
            total_limit=total_limit,
            base_cooldown_seconds=base_cooldown_seconds,
            max_cooldown_seconds=max_cooldown_seconds,
            backoff_factor=backoff_factor,
        )
        raw_entries = snap.get("entries")
        if isinstance(raw_entries, Mapping):
            for item in raw_entries.values():
                if isinstance(item, Mapping):
                    phase_val = item.get("phase")
                    agent_val = item.get("agent")
                    if not isinstance(phase_val, str) or not isinstance(agent_val, str):
                        continue
                    if not phase_val or not agent_val:
                        continue
                    consecutive_val = item.get("consecutive_unproductive")
                    total_val = item.get("total_unproductive")
                    until_val = item.get("cooldown_until_ms")
                    reason_val = item.get("cooldown_reason")
                    cooldown_count_val = item.get("cooldown_count")
                    is_spent_val = item.get("is_spent")
                    spent_reason_val = item.get("spent_reason")

                    entry = _LedgerEntry(
                        consecutive_unproductive=int(consecutive_val)
                        if isinstance(consecutive_val, (int, float, str))
                        else 0,
                        total_unproductive=int(total_val)
                        if isinstance(total_val, (int, float, str))
                        else 0,
                        cooldown_until_ms=int(until_val)
                        if isinstance(until_val, (int, float, str))
                        else None,
                        cooldown_reason=str(reason_val) if isinstance(reason_val, str) else None,
                        cooldown_count=int(cooldown_count_val)
                        if isinstance(cooldown_count_val, (int, float, str))
                        else 0,
                        is_spent=bool(is_spent_val) if is_spent_val is not None else False,
                        spent_reason=str(spent_reason_val)
                        if isinstance(spent_reason_val, str)
                        else None,
                    )
                    ledger._entries[(phase_val, agent_val)] = entry
        return ledger


__all__ = [
    "AgentFailureLedger",
    "AgentStatus",
    "AttemptFact",
    "AttemptOutcome",
    "Cooldown",
    "Eligible",
    "Spent",
]
