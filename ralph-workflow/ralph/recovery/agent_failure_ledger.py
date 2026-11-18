from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from loguru import logger

from ralph.recovery.agent_unavailability_tracker import (
    AgentUnavailabilityTracker,
    UnavailabilityStore,
    cooldown_windows,
)
from ralph.recovery.eligibility_wait import EligibilityWait
from ralph.recovery.legacy_guard_migration import (
    _LegacyGuardSource,
    translate_legacy_entry,
)
from ralph.recovery.legacy_guard_migration import (
    migrate_legacy_guard_history as _migrate_legacy_guard_history,
)
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.recovery.wait_kind import WaitKind

if TYPE_CHECKING:
    from ralph.agents.clock import Clock

LEGACY_DEFAULT_AGENT: Final[str] = "claude"
LEDGER_VERSION: Final[int] = 1
_LEDGER_VERSION_DRIFT_NOTICES: deque[str] = deque(maxlen=256)

LEGACY_IDENTITY_UNATTRIBUTABLE_REASON: Final[str] = "legacy_identity_unattributable"

LEDGER_SNAPSHOT_MALFORMED_REASON: Final[str] = "ledger_snapshot_malformed"

_DEFAULT_CONSECUTIVE_LIMIT: Final[int] = 3
_DEFAULT_TOTAL_LIMIT: Final[int] = 9
_DEFAULT_BASE_COOLDOWN_SECONDS: Final[float] = 60.0
_DEFAULT_MAX_COOLDOWN_SECONDS: Final[float] = 3600.0
_DEFAULT_BACKOFF_FACTOR: Final[float] = 2.0


@dataclass(frozen=True)
class _AttemptFact:
    phase: str
    agent: str
    artifact_valid: bool
    workspace_changed: bool | None = False
    free_retry_category: bool = False


class _AgentStatus:
    reason: str = ""

    @property
    def is_eligible(self) -> bool:
        return isinstance(self, _Eligible)

    @property
    def is_cooldown(self) -> bool:
        return isinstance(self, _Cooldown)


@dataclass(frozen=True)
class _Eligible(_AgentStatus):
    pass


@dataclass(frozen=True)
class _Cooldown(_AgentStatus):
    until_ms: int
    reason: str

    @property
    def until(self) -> int:
        return self.until_ms


@dataclass(frozen=True)
class _AttemptOutcome:
    status: _AgentStatus
    consecutive_unproductive: int
    total_unproductive: int

    @property
    def cooldown_started(self) -> bool:
        return isinstance(self.status, _Cooldown)

    @property
    def reason(self) -> str | None:
        status = self.status
        match status:
            case Cooldown():
                return status.reason
            case _:
                return None


AttemptFact = _AttemptFact
AgentStatus = _AgentStatus
Eligible = _Eligible
Cooldown = _Cooldown
CoolingDown = _Cooldown
AttemptOutcome = _AttemptOutcome
type AgentEligibility = Eligible | CoolingDown


def _coerce_optional_int(raw: object) -> int | None:
    match raw:
        case bool():
            return int(raw)
        case int():
            return raw
        case float():
            return int(raw)
        case str():
            return int(raw)
        case _:
            return None


def _coerce_optional_float(raw: object) -> float | None:
    match raw:
        case bool():
            return float(raw)
        case int() | float() | str():
            return float(raw)
        case _:
            return None


def _snapshot_counter(raw: object) -> int:
    match raw:
        case int() | float():
            return max(0, int(raw))
        case str():
            try:
                return max(0, int(raw.strip()))
            except ValueError:
                return 0
        case _:
            return 0


def _snapshot_entry_strictness(
    entry: Mapping[str, object],
    consecutive_limit: int,
    total_limit: int,
) -> tuple[int, int, int]:
    consecutive = _snapshot_counter(entry.get("consecutive_unproductive"))
    total = _snapshot_counter(entry.get("total_unproductive"))
    remaining = max(0, min(consecutive_limit - consecutive, total_limit - total))
    return (remaining, -total, -consecutive)


def migrate_legacy_guard_history(
    sources: Sequence[_LegacyGuardSource],
    *,
    existing_snapshot: Mapping[str, object] | None = None,
) -> dict[str, object] | None:
    return _migrate_legacy_guard_history(
        sources,
        entry_strictness=_snapshot_entry_strictness,
        existing_snapshot=existing_snapshot,
    )


def _optional_int_or_default(raw: object, default: int) -> int:
    value = _coerce_optional_int(raw)
    return default if value is None else value


def _optional_float_or_default(raw: object, default: float) -> float:
    value = _coerce_optional_float(raw)
    return default if value is None else value


@dataclass
class _LedgerEntry:
    consecutive_unproductive: int = 0
    total_unproductive: int = 0
    cooldown_count: int = 0


def eligibility_wait_from_statuses(
    statuses: Sequence[AgentStatus],
    *,
    now_ms: int,
) -> EligibilityWait:
    if not statuses:
        return EligibilityWait(kind=WaitKind.NO_CANDIDATES, wait_ms=None)
    if any(status.is_eligible for status in statuses):
        return EligibilityWait(kind=WaitKind.AVAILABLE_NOW, wait_ms=0)
    cooldowns = [status for status in statuses if isinstance(status, Cooldown)]
    earliest = cooldowns[0]
    for candidate in cooldowns[1:]:
        if candidate.until_ms < earliest.until_ms:
            earliest = candidate
    return EligibilityWait(
        kind=WaitKind.COOLING_DOWN,
        wait_ms=max(1, earliest.until_ms - now_ms),
        until_ms=earliest.until_ms,
        reason=earliest.reason,
    )


def cooldown_status(store: UnavailabilityStore, agent: str, *, now_ms: int) -> AgentStatus:
    window = cooldown_windows(store, now_ms=now_ms).get(agent)
    if window is None:
        return Eligible()
    return Cooldown(until_ms=window[0], reason=window[1])


class AgentFailureLedger:
    __slots__ = (
        "_availability",
        "_backoff_factor",
        "_base_cooldown_seconds",
        "_clock",
        "_consecutive_limit",
        "_entries",
        "_lock",
        "_max_cooldown_seconds",
        "_recovery_controller",
        "_total_limit",
    )

    def __init__(
        self,
        *,
        clock: Clock,
        consecutive_limit: int = _DEFAULT_CONSECUTIVE_LIMIT,
        total_limit: int = _DEFAULT_TOTAL_LIMIT,
        base_cooldown_seconds: float = _DEFAULT_BASE_COOLDOWN_SECONDS,
        max_cooldown_seconds: float = _DEFAULT_MAX_COOLDOWN_SECONDS,
        backoff_factor: float = _DEFAULT_BACKOFF_FACTOR,
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
        self._recovery_controller: object | None = None
        self._availability: UnavailabilityStore | None = None
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], _LedgerEntry] = {}

    @property
    def recovery_controller(self) -> object | None:
        return self._recovery_controller

    @recovery_controller.setter
    def recovery_controller(self, controller: object) -> None:
        self._recovery_controller = controller

    @property
    def availability(self) -> UnavailabilityStore:
        if self._availability is None:
            self._availability = AgentUnavailabilityTracker(clock=self._clock, ledger=self)
        return self._availability

    @property
    def bound_availability(self) -> UnavailabilityStore | None:
        return self._availability

    def bind_availability(self, store: UnavailabilityStore) -> None:
        self._availability = store

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def consecutive_limit(self) -> int:
        return self._consecutive_limit

    @property
    def total_limit(self) -> int:
        return self._total_limit

    @property
    def base_cooldown_seconds(self) -> float:
        return self._base_cooldown_seconds

    @property
    def max_cooldown_seconds(self) -> float:
        return self._max_cooldown_seconds

    @property
    def backoff_factor(self) -> float:
        return self._backoff_factor

    def _now_ms(self) -> int:
        return int(self._clock.monotonic() * 1000)

    def status(self, phase: str, agent: str) -> AgentStatus:
        del phase
        return cooldown_status(self.availability, agent, now_ms=self._now_ms())

    def agent_eligibility(self, phase: str, agent: str) -> AgentEligibility:
        status = self.status(phase, agent)
        if isinstance(status, Cooldown):
            return status
        return Eligible()

    def is_available(self, phase: str, agent: str) -> bool:
        return self.status(phase, agent).is_eligible

    def eligibility(
        self,
        phase: str,
        agents: Sequence[str],
    ) -> tuple[tuple[str, AgentStatus], ...]:
        return tuple((agent, self.status(phase, agent)) for agent in agents)

    def eligibility_wait(self, phase: str, agents: Sequence[str]) -> EligibilityWait:
        statuses = [status for _agent, status in self.eligibility(phase, agents)]
        return eligibility_wait_from_statuses(statuses, now_ms=self._now_ms())

    def any_eligible(self, phase: str, agents: Sequence[str] = ()) -> bool:
        effective_agents = tuple(agents) or self.registered_agents(phase)
        if not effective_agents:
            return True
        return any(
            status.is_eligible for _agent, status in self.eligibility(phase, effective_agents)
        )

    def registered_agents(self, phase: str) -> tuple[str, ...]:
        with self._lock:
            return tuple(agent for entry_phase, agent in self._entries if entry_phase == phase)

    def earliest_wait_ms(self, phase: str, agents: Sequence[str]) -> int:
        wait = self.eligibility_wait(phase, agents)
        if wait.kind is WaitKind.COOLING_DOWN and wait.wait_ms is not None:
            return wait.wait_ms
        return 0

    def failure_reason(self, phase: str, agent: str) -> str | None:
        status = self.status(phase, agent)
        match status:
            case Cooldown():
                return status.reason
            case _:
                return None

    def _limit_cooldown_ms(self, entry: _LedgerEntry) -> int:
        exponent = min(entry.cooldown_count, 30)
        duration_seconds = min(
            self._base_cooldown_seconds * (self._backoff_factor**exponent),
            self._max_cooldown_seconds,
        )
        return max(1, int(duration_seconds * 1000))

    def record(self, fact: AttemptFact) -> AttemptOutcome:
        with self._lock:
            key = (fact.phase, fact.agent)
            entry = self._entries.get(key)
            if entry is None:
                entry = _LedgerEntry()
                self._entries[key] = entry

            if fact.artifact_valid:
                entry.consecutive_unproductive = 0
                entry.total_unproductive = 0
                entry.cooldown_count = 0
                return AttemptOutcome(
                    status=Eligible(),
                    consecutive_unproductive=0,
                    total_unproductive=0,
                )

            if fact.workspace_changed is not True:
                entry.consecutive_unproductive += 1
            else:
                entry.consecutive_unproductive = 0
            entry.total_unproductive += 1
            consecutive = entry.consecutive_unproductive
            total = entry.total_unproductive
            if consecutive < self._consecutive_limit and total < self._total_limit:
                return AttemptOutcome(
                    status=Eligible(),
                    consecutive_unproductive=consecutive,
                    total_unproductive=total,
                )
            duration_ms = self._limit_cooldown_ms(entry)
            entry.consecutive_unproductive = 0
            entry.total_unproductive = 0
            entry.cooldown_count += 1
        marked = self.availability.mark_unavailable(
            fact.phase,
            fact.agent,
            UnavailabilityReason.UNPRODUCTIVE_LIMIT,
            duration_ms=duration_ms,
        )
        return AttemptOutcome(
            status=Cooldown(
                until_ms=marked.unavailable_until_ms,
                reason=str(UnavailabilityReason.UNPRODUCTIVE_LIMIT),
            ),
            consecutive_unproductive=consecutive,
            total_unproductive=total,
        )

    def _snapshot_locked(self) -> dict[str, object]:
        entries_dict: dict[str, object] = {
            f"{phase}:{agent}": {
                "phase": phase,
                "agent": agent,
                "consecutive_unproductive": entry.consecutive_unproductive,
                "total_unproductive": entry.total_unproductive,
                "cooldown_count": entry.cooldown_count,
            }
            for (phase, agent), entry in self._entries.items()
        }
        return {
            "version": LEDGER_VERSION,
            "consecutive_limit": self._consecutive_limit,
            "total_limit": self._total_limit,
            "base_cooldown_seconds": self._base_cooldown_seconds,
            "max_cooldown_seconds": self._max_cooldown_seconds,
            "backoff_factor": self._backoff_factor,
            "entries": entries_dict,
        }

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return self._snapshot_locked()

    @classmethod
    def translate_legacy_entry(
        cls,
        guard_data: Mapping[str, object],
        phase: str,
        agent: str,
        *,
        consecutive_limit: int = _DEFAULT_CONSECUTIVE_LIMIT,
        total_limit: int = _DEFAULT_TOTAL_LIMIT,
    ) -> dict[str, object]:
        return translate_legacy_entry(
            guard_data,
            phase,
            agent,
            consecutive_limit=consecutive_limit,
            total_limit=total_limit,
        )

    @classmethod
    def _from_legacy_snapshot(
        cls,
        guard_data: Mapping[str, object],
        *,
        clock: Clock,
        phase: str = "development",
        agent: str = LEGACY_DEFAULT_AGENT,
        consecutive_limit: int = _DEFAULT_CONSECUTIVE_LIMIT,
        total_limit: int = _DEFAULT_TOTAL_LIMIT,
    ) -> AgentFailureLedger:
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
        ledger._entries[(phase, agent)] = _LedgerEntry(
            consecutive_unproductive=_optional_int_or_default(
                entry_dict["consecutive_unproductive"], 0
            ),
            total_unproductive=_optional_int_or_default(entry_dict["total_unproductive"], 0),
        )
        return ledger

    @classmethod
    def from_snapshot(
        cls,
        snap: Mapping[str, object],
        *,
        clock: Clock,
        notice: Callable[[str], None] | None = None,
    ) -> AgentFailureLedger:
        raw_version = snap.get("version")
        if raw_version is not None and raw_version != LEDGER_VERSION:
            message = (
                f"Agent failure ledger version {raw_version!r} is unsupported; "
                f"rebuilt as version {LEDGER_VERSION}."
            )
            notice_key = repr(raw_version)
            if notice_key not in _LEDGER_VERSION_DRIFT_NOTICES:
                _LEDGER_VERSION_DRIFT_NOTICES.append(notice_key)
                if notice is None:
                    logger.warning("{}", message)
                else:
                    notice(message)
            return cls(clock=clock)
        if "entries" not in snap and (
            "streak" in snap or "max_identical" in snap or "failed_closed" in snap
        ):
            return cls._from_legacy_snapshot(snap, clock=clock)
        ledger = cls(
            clock=clock,
            consecutive_limit=_optional_int_or_default(
                snap.get("consecutive_limit"), _DEFAULT_CONSECUTIVE_LIMIT
            ),
            total_limit=_optional_int_or_default(snap.get("total_limit"), _DEFAULT_TOTAL_LIMIT),
            base_cooldown_seconds=_optional_float_or_default(
                snap.get("base_cooldown_seconds"), _DEFAULT_BASE_COOLDOWN_SECONDS
            ),
            max_cooldown_seconds=_optional_float_or_default(
                snap.get("max_cooldown_seconds"), _DEFAULT_MAX_COOLDOWN_SECONDS
            ),
            backoff_factor=_optional_float_or_default(
                snap.get("backoff_factor"), _DEFAULT_BACKOFF_FACTOR
            ),
        )
        raw_entries = snap.get("entries")
        if isinstance(raw_entries, Mapping):
            for item in raw_entries.values():
                if not isinstance(item, Mapping):
                    continue
                phase_val = item.get("phase")
                agent_val = item.get("agent")
                if not isinstance(phase_val, str) or not isinstance(agent_val, str):
                    continue
                if not phase_val or not agent_val:
                    continue
                ledger._entries[(phase_val, agent_val)] = _LedgerEntry(
                    consecutive_unproductive=_snapshot_counter(
                        item.get("consecutive_unproductive")
                    ),
                    total_unproductive=_snapshot_counter(item.get("total_unproductive")),
                    cooldown_count=_snapshot_counter(item.get("cooldown_count")),
                )
        return ledger


__all__ = [
    "LEDGER_SNAPSHOT_MALFORMED_REASON",
    "LEDGER_VERSION",
    "LEGACY_DEFAULT_AGENT",
    "LEGACY_IDENTITY_UNATTRIBUTABLE_REASON",
    "AgentEligibility",
    "AgentFailureLedger",
    "AgentStatus",
    "AttemptFact",
    "AttemptOutcome",
    "Cooldown",
    "CoolingDown",
    "EligibilityWait",
    "Eligible",
    "WaitKind",
    "_LegacyGuardSource",
    "cooldown_status",
    "eligibility_wait_from_statuses",
    "migrate_legacy_guard_history",
]
