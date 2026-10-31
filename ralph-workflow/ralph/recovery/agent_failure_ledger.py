"""Agent failure ledger: single-owner component for agent failure and demotion decisions.

This component tracks consecutive and total unproductive execution attempts per
(phase, agent) pair and decides whether an agent remains eligible, enters cooldown,
or becomes permanently spent.

All decisions are pure functions of observed facts and injected time; no ambient
system reads (clock, filesystem, process table) are performed.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from ralph.agents.clock import Clock

LEGACY_DEFAULT_AGENT: Final[str] = "claude"
"""Agent name assumed when a legacy checkpoint carries no recoverable agent identity."""

_LEGACY_MALFORMED_REASON: Final[str] = "legacy_guard_malformed"
"""Demotion reason recorded when legacy failure history cannot be read."""

LEGACY_IDENTITY_UNATTRIBUTABLE_REASON: Final[str] = "legacy_identity_unattributable"
"""Demotion reason recorded when legacy history belongs to no recoverable agent."""

LEDGER_SNAPSHOT_MALFORMED_REASON: Final[str] = "ledger_snapshot_malformed"
"""Demotion reason recorded when the stored ledger snapshot itself is unreadable."""

_DEFAULT_CONSECUTIVE_LIMIT: Final[int] = 3
_DEFAULT_TOTAL_LIMIT: Final[int] = 9
_DEFAULT_BASE_COOLDOWN_SECONDS: Final[float] = 60.0
_DEFAULT_MAX_COOLDOWN_SECONDS: Final[float] = 3600.0
_DEFAULT_BACKOFF_FACTOR: Final[float] = 2.0


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


@dataclass(frozen=True)
class _LegacyGuardSource:
    """A legacy failure-history payload together with its recovered identity.

    Legacy checkpoints stored one ``progress_guard`` payload per phase chain
    plus a top-level copy describing the active phase. Every payload described
    the failure history of exactly ONE agent, so each source is migrated
    independently into its own ledger entry.

    Attributes:
        payload: Raw legacy payload (a mapping for well-formed checkpoints).
        phase: Canonical phase name the payload belongs to.
        agent: Recovered agent name, or ``None`` when the identity could not
            be recovered from the legacy chain.
        candidate_agents: Agents declared by the legacy chain. Used when the
            current agent of that chain cannot be recovered, so the recorded
            history is preserved for the agent that actually failed.
        fail_closed_reason: When set, the affected identity is failed closed
            with this reason instead of being translated.
    """

    payload: object
    phase: str
    agent: str | None = None
    candidate_agents: tuple[str, ...] = ()
    fail_closed_reason: str | None = None


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
        """Return a serializable dictionary snapshot of ledger state.

        ``cooldown_until_ms`` is a deadline on this process's monotonic clock,
        which restarts on reboot, so each active cooldown is also persisted as
        a wall-clock deadline (``cooldown_until_epoch_ms``) that
        :meth:`from_snapshot` re-anchors onto the restoring clock.
        """
        with self._lock:
            now_ms = int(self._clock.monotonic() * 1000)
            now_epoch_ms = int(self._clock.epoch() * 1000)
            entries_dict: dict[str, object] = {
                f"{phase}:{agent}": {
                    "phase": phase,
                    "agent": agent,
                    "consecutive_unproductive": entry.consecutive_unproductive,
                    "total_unproductive": entry.total_unproductive,
                    "cooldown_until_ms": entry.cooldown_until_ms,
                    "cooldown_until_epoch_ms": None
                    if entry.cooldown_until_ms is None
                    else now_epoch_ms + max(0, entry.cooldown_until_ms - now_ms),
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
    def translate_legacy_entry(
        cls,
        guard_data: Mapping[str, object],
        phase: str,
        agent: str,
        *,
        consecutive_limit: int = 3,
        total_limit: int = 9,
    ) -> dict[str, object]:
        """Translate a legacy progress snapshot dict into a single ledger entry snapshot."""
        raw_streak = guard_data.get("streak", 0)
        streak = (
            int(raw_streak)
            if isinstance(raw_streak, (int, float, str)) and not isinstance(raw_streak, bool)
            else 0
        )
        streak = max(0, streak)
        is_exhausted = bool(guard_data.get("is_exhausted", False))
        failed_closed = bool(guard_data.get("failed_closed", False))
        is_spent = is_exhausted or failed_closed or streak >= consecutive_limit
        consecutive = max(streak, consecutive_limit) if is_spent else min(streak, consecutive_limit)
        return {
            "phase": phase,
            "agent": agent,
            "consecutive_unproductive": consecutive,
            "total_unproductive": min(consecutive, total_limit),
            "cooldown_until_ms": None,
            "cooldown_reason": None,
            "cooldown_count": 0,
            "is_spent": is_spent,
            "spent_reason": "legacy_exhausted" if is_spent else None,
        }

    @classmethod
    def _from_legacy_snapshot(
        cls,
        guard_data: Mapping[str, object],
        *,
        clock: Clock,
        phase: str = "development",
        agent: str = "claude",
        consecutive_limit: int = 3,
        total_limit: int = 9,
    ) -> AgentFailureLedger:
        """Construct an AgentFailureLedger translated from a legacy snapshot."""
        ledger = cls(
            clock=clock,
            consecutive_limit=consecutive_limit,
            total_limit=total_limit,
        )
        entry_dict = cls.translate_legacy_entry(
            guard_data,
            phase,
            agent,
            consecutive_limit=consecutive_limit,
            total_limit=total_limit,
        )
        raw_consecutive = entry_dict["consecutive_unproductive"]
        consecutive = int(raw_consecutive) if isinstance(raw_consecutive, (int, float, str)) else 0
        raw_total = entry_dict["total_unproductive"]
        total = int(raw_total) if isinstance(raw_total, (int, float, str)) else 0
        raw_spent_reason = entry_dict["spent_reason"]
        entry = _LedgerEntry(
            consecutive_unproductive=consecutive,
            total_unproductive=total,
            cooldown_until_ms=None,
            cooldown_reason=None,
            cooldown_count=0,
            is_spent=bool(entry_dict["is_spent"]),
            spent_reason=raw_spent_reason if isinstance(raw_spent_reason, str) else None,
        )
        ledger._entries[(phase, agent)] = entry
        return ledger

    @classmethod
    def from_snapshot(
        cls,
        snap: Mapping[str, object],
        *,
        clock: Clock,
    ) -> AgentFailureLedger:
        """Reconstruct an AgentFailureLedger from a snapshot dictionary."""
        if "entries" not in snap and (
            "streak" in snap or "max_identical" in snap or "failed_closed" in snap
        ):
            return cls._from_legacy_snapshot(snap, clock=clock)
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
        now_mono_ms = int(clock.monotonic() * 1000)
        now_epoch_ms = int(clock.epoch() * 1000)
        max_cooldown_ms = int(ledger.max_cooldown_seconds * 1000)
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
                    until_ms = _restored_cooldown_until_ms(
                        item,
                        now_mono_ms=now_mono_ms,
                        now_epoch_ms=now_epoch_ms,
                        max_cooldown_ms=max_cooldown_ms,
                    )
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
                        cooldown_until_ms=until_ms,
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


def _restored_cooldown_until_ms(
    item: Mapping[str, object],
    *,
    now_mono_ms: int,
    now_epoch_ms: int,
    max_cooldown_ms: int,
) -> int | None:
    """Re-anchor a persisted cooldown deadline onto the restoring monotonic clock.

    The wall-clock deadline (``cooldown_until_epoch_ms``) survives a reboot,
    so the remaining duration is measured against the restoring clock's
    epoch. Legacy snapshots carry only the previous boot's monotonic deadline
    (``cooldown_until_ms``); it is measured against the restoring monotonic
    clock. Either way the remaining duration is clamped to
    ``[0, max_cooldown_ms]`` -- no cooldown the ledger can grant outlasts its
    maximum, so a deadline from a different clock domain can never strand an
    agent for the previous uptime. A zero remainder restores as an elapsed
    cooldown that the next status read expires normally.
    """
    epoch_val = item.get("cooldown_until_epoch_ms")
    until_val = item.get("cooldown_until_ms")
    if isinstance(epoch_val, (int, float)) and not isinstance(epoch_val, bool):
        remaining_ms = int(epoch_val) - now_epoch_ms
    elif isinstance(until_val, (int, float, str)) and not isinstance(until_val, bool):
        try:
            remaining_ms = int(float(until_val)) - now_mono_ms
        except ValueError:
            return None
    else:
        return None
    return now_mono_ms + min(max(remaining_ms, 0), max_cooldown_ms)


def _counter_value(raw: object) -> int:
    """Return a non-negative counter value, or 0 when the value is unusable."""
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return max(0, raw)
    if isinstance(raw, float):
        return max(0, int(raw))
    if isinstance(raw, str):
        try:
            return max(0, int(raw.strip()))
        except ValueError:
            return 0
    return 0


def _float_value(raw: object, default: float) -> float:
    """Return a float value, or the default when the value is unusable."""
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int | float):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.strip())
        except ValueError:
            return default
    return default


def _readable_streak(raw: object) -> int | None:
    """Return the streak count when the value is a readable non-negative count."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        return int(raw) if raw >= 0 else None
    if isinstance(raw, str):
        try:
            parsed = int(raw.strip())
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def _readable_legacy_field(field: str, raw: object) -> bool:
    """Return True when a recognised legacy field holds a usable value."""
    if field == "streak":
        return _readable_streak(raw) is not None
    if field == "max_identical":
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            return False
        return _counter_value(raw) > 0
    return raw is None or isinstance(raw, bool)


def _readable_legacy_payload(payload: object) -> Mapping[str, object] | None:
    """Return the payload as a mapping when it carries readable failure history.

    Returns ``None`` when the payload cannot be read: it is not a mapping, it
    carries none of the recognised failure-history fields, or a recognised
    field holds a value that cannot be interpreted (a non-numeric streak, a
    non-positive cap, a non-boolean exhaustion flag).
    """
    if not isinstance(payload, Mapping):
        return None
    fields = ("streak", "max_identical", "is_exhausted", "failed_closed")
    if not any(field in payload for field in fields):
        return None
    for field in fields:
        if field in payload and not _readable_legacy_field(field, payload[field]):
            return None
    return payload


def _legacy_identities(source: _LegacyGuardSource) -> tuple[tuple[str, str], ...]:
    """Return the (phase, agent) identities a legacy source applies to.

    A recoverable agent identity wins. When the current agent of a legacy chain
    cannot be recovered, the recorded history is applied to every agent the
    chain declares, so the history is preserved for the agent that actually
    failed. When the chain declares no agent at all, the legacy default agent
    name is used so the history is never discarded.
    """
    if source.agent:
        return ((source.phase, source.agent),)
    if source.candidate_agents:
        return tuple((source.phase, agent) for agent in source.candidate_agents)
    return ((source.phase, LEGACY_DEFAULT_AGENT),)


def _failed_closed_entry(
    phase: str,
    agent: str,
    *,
    reason: str,
    consecutive_limit: int,
    total_limit: int,
) -> dict[str, object]:
    """Return a ledger entry that denies further attempts for an agent."""
    return {
        "phase": phase,
        "agent": agent,
        "consecutive_unproductive": consecutive_limit,
        "total_unproductive": total_limit,
        "cooldown_until_ms": None,
        "cooldown_reason": None,
        "cooldown_count": 0,
        "is_spent": True,
        "spent_reason": reason,
    }


def _entry_strictness(
    entry: Mapping[str, object],
    *,
    consecutive_limit: int,
    total_limit: int,
) -> tuple[int, int, int, int]:
    """Return a sort key for a ledger entry where a smaller tuple is stricter.

    Strictness is measured by how many further unproductive attempts the
    agent can still absorb before it is demoted: a spent entry is strictest,
    then the entry with the fewest remaining attempts, then the entry that
    recorded the most failures.
    """
    spent = 0 if bool(entry.get("is_spent")) else 1
    consecutive = _counter_value(entry.get("consecutive_unproductive"))
    total = _counter_value(entry.get("total_unproductive"))
    remaining = max(0, min(consecutive_limit - consecutive, total_limit - total))
    return (spent, remaining, -total, -consecutive)


def _snapshot_limits(
    snapshot: Mapping[str, object] | None,
) -> tuple[int, int, float, float, float]:
    """Return the ledger limits of a snapshot, falling back to the defaults."""
    if snapshot is None:
        return (
            _DEFAULT_CONSECUTIVE_LIMIT,
            _DEFAULT_TOTAL_LIMIT,
            _DEFAULT_BASE_COOLDOWN_SECONDS,
            _DEFAULT_MAX_COOLDOWN_SECONDS,
            _DEFAULT_BACKOFF_FACTOR,
        )
    consecutive = _counter_value(snapshot.get("consecutive_limit")) or _DEFAULT_CONSECUTIVE_LIMIT
    total = _counter_value(snapshot.get("total_limit")) or _DEFAULT_TOTAL_LIMIT
    base = _float_value(snapshot.get("base_cooldown_seconds"), _DEFAULT_BASE_COOLDOWN_SECONDS)
    maximum = _float_value(snapshot.get("max_cooldown_seconds"), _DEFAULT_MAX_COOLDOWN_SECONDS)
    backoff = _float_value(snapshot.get("backoff_factor"), _DEFAULT_BACKOFF_FACTOR)
    return (consecutive, total, base, maximum, backoff)


def _translate_legacy_source(
    source: _LegacyGuardSource,
    phase: str,
    agent: str,
    *,
    consecutive_limit: int,
    total_limit: int,
) -> dict[str, object]:
    """Translate one legacy payload into a ledger entry for a single identity."""
    if source.fail_closed_reason is not None:
        return _failed_closed_entry(
            phase,
            agent,
            reason=source.fail_closed_reason,
            consecutive_limit=consecutive_limit,
            total_limit=total_limit,
        )
    payload = _readable_legacy_payload(source.payload)
    if payload is None:
        return _failed_closed_entry(
            phase,
            agent,
            reason=_LEGACY_MALFORMED_REASON,
            consecutive_limit=consecutive_limit,
            total_limit=total_limit,
        )
    legacy_cap = _counter_value(payload.get("max_identical"))
    effective_limit = consecutive_limit
    if legacy_cap > 0:
        effective_limit = min(consecutive_limit, legacy_cap)
    return AgentFailureLedger.translate_legacy_entry(
        payload,
        phase,
        agent,
        consecutive_limit=effective_limit,
        total_limit=total_limit,
    )


def migrate_legacy_guard_history(
    sources: Sequence[_LegacyGuardSource],
    *,
    existing_snapshot: Mapping[str, object] | None = None,
) -> dict[str, object] | None:
    """Migrate legacy per-chain failure history into a durable ledger snapshot.

    Each source is migrated independently into its own entry keyed
    ``"{phase}:{agent}"``. When a stored ledger snapshot is present, the
    stricter of the stored entry and the migrated entry is kept for every
    identity, so translation can never lower demotion protection.

    Unreadable legacy data fails closed: the affected agent is recorded as
    spent with an explicit reason instead of being granted a fresh attempt
    budget.

    Returns:
        The merged snapshot, or ``None`` when there is nothing to migrate.
    """
    if not sources:
        return None
    consecutive_limit, total_limit, base, maximum, backoff = _snapshot_limits(existing_snapshot)
    entries: dict[str, dict[str, object]] = {}
    if existing_snapshot is not None:
        raw_entries = existing_snapshot.get("entries")
        if isinstance(raw_entries, Mapping):
            for key, item in raw_entries.items():
                if isinstance(item, Mapping):
                    entries[str(key)] = dict(item)
    for source in sources:
        for phase, agent in _legacy_identities(source):
            migrated = _translate_legacy_source(
                source,
                phase,
                agent,
                consecutive_limit=consecutive_limit,
                total_limit=total_limit,
            )
            key = f"{phase}:{agent}"
            stored = entries.get(key)
            if stored is None:
                entries[key] = migrated
                continue
            if _entry_strictness(
                migrated,
                consecutive_limit=consecutive_limit,
                total_limit=total_limit,
            ) < _entry_strictness(
                stored,
                consecutive_limit=consecutive_limit,
                total_limit=total_limit,
            ):
                entries[key] = migrated
    return {
        "consecutive_limit": consecutive_limit,
        "total_limit": total_limit,
        "base_cooldown_seconds": base,
        "max_cooldown_seconds": maximum,
        "backoff_factor": backoff,
        "entries": entries,
    }


__all__ = [
    "LEDGER_SNAPSHOT_MALFORMED_REASON",
    "LEGACY_DEFAULT_AGENT",
    "LEGACY_IDENTITY_UNATTRIBUTABLE_REASON",
    "AgentFailureLedger",
    "AgentStatus",
    "AttemptFact",
    "AttemptOutcome",
    "Cooldown",
    "Eligible",
    "Spent",
    "_LegacyGuardSource",
    "migrate_legacy_guard_history",
]
