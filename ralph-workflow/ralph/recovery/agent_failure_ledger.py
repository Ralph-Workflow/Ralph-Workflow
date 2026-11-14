from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from loguru import logger

from ralph.recovery.eligibility_wait import EligibilityWait
from ralph.recovery.legacy_guard_migration import (
    _LegacyGuardSource,
    migrate_legacy_guard_history,
    translate_legacy_entry,
)
from ralph.recovery.wait_kind import WaitKind

if TYPE_CHECKING:
    from ralph.agents.clock import Clock

LEGACY_DEFAULT_AGENT: Final[str] = "claude"
LEDGER_VERSION: Final[int] = 1
_LEDGER_VERSION_DRIFT_NOTICES: deque[str] = deque(maxlen=256)

_LEGACY_MALFORMED_REASON: Final[str] = "legacy_guard_malformed"

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
    @property
    def is_eligible(self) -> bool:
        return isinstance(self, _Eligible)

    @property
    def is_cooldown(self) -> bool:
        return isinstance(self, _Cooldown)

    @property
    def is_spent(self) -> bool:
        return isinstance(self, _Spent)

    @property
    def is_unavailable(self) -> bool:
        return isinstance(self, _Unavailable)


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
class _Spent(_AgentStatus):
    reason: str


@dataclass(frozen=True)
class _Unavailable(_AgentStatus):
    reason: str


@dataclass(frozen=True)
class _AttemptOutcome:
    status: _AgentStatus
    consecutive_unproductive: int
    total_unproductive: int

    @property
    def demoted(self) -> bool:
        return not isinstance(self.status, _Eligible)

    @property
    def reason(self) -> str | None:
        if isinstance(self.status, (_Cooldown, _Spent)):
            return self.status.reason
        return None


AttemptFact = _AttemptFact
AgentStatus = _AgentStatus
Eligible = _Eligible
Cooldown = _Cooldown
CoolingDown = _Cooldown
Spent = _Spent
Unavailable = _Unavailable
AttemptOutcome = _AttemptOutcome
type AgentEligibility = Eligible | CoolingDown | Spent | Unavailable


@dataclass
class _LedgerEntry:
    consecutive_unproductive: int = 0
    total_unproductive: int = 0
    cooldown_until_ms: int | None = None
    cooldown_reason: str | None = None
    cooldown_count: int = 0
    is_spent: bool = False
    spent_reason: str | None = None
    unavailable_reason: str | None = None


_COOLDOWN_REASON_CONSECUTIVE = "consecutive_unproductive_limit_reached"
_SPENT_REASON_TOTAL = "total_unproductive_limit_reached"


def _optional_str(raw: object) -> str | None:
    return raw if isinstance(raw, str) else None


def _optional_int(raw: object) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    return None


def _projected_status(
    *,
    is_spent: bool,
    spent_reason: str | None,
    cooldown_until_ms: int | None,
    cooldown_reason: str | None,
    unavailable_reason: str | None,
    now_ms: int,
) -> AgentStatus:
    if is_spent:
        return Spent(reason=spent_reason or _SPENT_REASON_TOTAL)
    if cooldown_until_ms is not None and now_ms < cooldown_until_ms:
        return Cooldown(
            until_ms=cooldown_until_ms,
            reason=cooldown_reason or _COOLDOWN_REASON_CONSECUTIVE,
        )
    if unavailable_reason is not None:
        return Unavailable(reason=unavailable_reason)
    return Eligible()


def _project_eligibility(
    snapshot: Mapping[str, object],
    *,
    phase: str,
    agent: str,
    now_ms: int,
) -> AgentStatus:
    entries = snapshot.get("entries")
    raw = entries.get(f"{phase}:{agent}") if isinstance(entries, Mapping) else None
    if not isinstance(raw, Mapping):
        return Eligible()
    return _projected_status(
        is_spent=bool(raw.get("is_spent")),
        spent_reason=_optional_str(raw.get("spent_reason")),
        cooldown_until_ms=_optional_int(raw.get("cooldown_until_ms")),
        cooldown_reason=_optional_str(raw.get("cooldown_reason")),
        unavailable_reason=_optional_str(raw.get("unavailable_reason")),
        now_ms=now_ms,
    )


def project_eligibility(
    snapshot: Mapping[str, object],
    *,
    phase: str,
    agent: str,
    clock: Clock,
) -> AgentStatus:
    now_ms = int(clock.monotonic() * 1000)
    return _project_eligibility(snapshot, phase=phase, agent=agent, now_ms=now_ms)


def project_chain_eligibility(
    snapshot: Mapping[str, object],
    *,
    phase: str,
    agents: Sequence[str],
    clock: Clock,
) -> tuple[tuple[str, AgentStatus], ...]:
    now_ms = int(clock.monotonic() * 1000)
    return tuple(
        (agent, _project_eligibility(snapshot, phase=phase, agent=agent, now_ms=now_ms))
        for agent in agents
    )


def eligibility_wait_from_statuses(
    statuses: Sequence[AgentStatus],
    *,
    now_ms: int,
) -> EligibilityWait:
    if any(status.is_eligible for status in statuses):
        return EligibilityWait(kind=WaitKind.AVAILABLE_NOW, wait_ms=0)
    cooldowns = [status for status in statuses if isinstance(status, Cooldown)]
    if cooldowns:
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
    reasons = [status.reason for status in statuses if isinstance(status, (Spent, Unavailable))]
    return EligibilityWait(
        kind=WaitKind.NEVER_ELIGIBLE,
        wait_ms=None,
        reason="; ".join(reasons) if reasons else None,
    )


class AgentFailureLedger:
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
        self._entries: dict[tuple[str, str], _LedgerEntry] = {}

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

    @property
    def clock(self) -> Clock:
        return self._clock

    def _sync_cooldown_expiry(self, entry: _LedgerEntry, now_ms: int) -> None:
        if entry.is_spent:
            return
        if entry.cooldown_until_ms is not None and now_ms >= entry.cooldown_until_ms:
            entry.cooldown_until_ms = None
            entry.cooldown_reason = None
            entry.consecutive_unproductive = 0

    def prune_expired(self, *, now_ms: int | None = None) -> None:
        with self._lock:
            effective_now_ms = now_ms if now_ms is not None else int(self._clock.monotonic() * 1000)
            for entry in self._entries.values():
                self._sync_cooldown_expiry(entry, effective_now_ms)

    def clear_agent_cooldown(self, phase: str, agent: str) -> None:
        with self._lock:
            entry = self._entries.get((phase, agent))
            if entry is not None and not entry.is_spent:
                entry.cooldown_until_ms = None
                entry.cooldown_reason = None
                entry.consecutive_unproductive = 0

    def _status_locked(self, phase: str, agent: str, now_ms: int) -> AgentStatus:
        entry = self._entries.get((phase, agent))
        if entry is None:
            return Eligible()

        if entry.is_spent:
            return Spent(reason=entry.spent_reason or _SPENT_REASON_TOTAL)

        self._sync_cooldown_expiry(entry, now_ms)

        return _projected_status(
            is_spent=False,
            spent_reason=None,
            cooldown_until_ms=entry.cooldown_until_ms,
            cooldown_reason=entry.cooldown_reason,
            unavailable_reason=entry.unavailable_reason,
            now_ms=now_ms,
        )

    def status(self, phase: str, agent: str) -> AgentStatus:
        with self._lock:
            now_ms = int(self._clock.monotonic() * 1000)
            return _project_eligibility(
                self._snapshot_locked(now_ms=now_ms),
                phase=phase,
                agent=agent,
                now_ms=now_ms,
            )

    def agent_eligibility(
        self,
        phase: str,
        agent: str,
        *,
        cooldown: tuple[int, str] | None = None,
        spent_reason: str | None = None,
        unavailable_reason: str | None = None,
    ) -> AgentEligibility:
        now_ms = int(self._clock.monotonic() * 1000)
        status = self.status(phase, agent)
        if isinstance(status, (Spent, Unavailable)):
            return status
        if isinstance(status, Cooldown) and (cooldown is None or status.until_ms <= cooldown[0]):
            return status
        if cooldown is not None and now_ms < cooldown[0]:
            return CoolingDown(until_ms=cooldown[0], reason=cooldown[1])
        if spent_reason is not None:
            return Spent(reason=spent_reason)
        if unavailable_reason is not None:
            return Unavailable(reason=unavailable_reason)
        return Eligible()

    def spent_status(self, reason: str) -> AgentStatus:
        return Spent(reason=reason)

    def is_available(self, phase: str, agent: str) -> bool:
        return self.status(phase, agent).is_eligible

    def eligibility(
        self,
        phase: str,
        agents: Sequence[str],
    ) -> tuple[tuple[str, AgentStatus], ...]:
        with self._lock:
            now_ms = int(self._clock.monotonic() * 1000)
            snapshot = self._snapshot_locked(now_ms=now_ms)
        return tuple(
            (agent, _project_eligibility(snapshot, phase=phase, agent=agent, now_ms=now_ms))
            for agent in agents
        )

    def eligibility_wait(self, phase: str, agents: Sequence[str]) -> EligibilityWait:
        with self._lock:
            now_ms = int(self._clock.monotonic() * 1000)
            snapshot = self._snapshot_locked(now_ms=now_ms)
        statuses = [
            _project_eligibility(snapshot, phase=phase, agent=agent, now_ms=now_ms)
            for agent in agents
        ]
        return eligibility_wait_from_statuses(statuses, now_ms=now_ms)

    def any_eligible(self, phase: str, agents: Sequence[str] = ()) -> bool:
        with self._lock:
            now_ms = int(self._clock.monotonic() * 1000)
            snapshot = self._snapshot_locked(now_ms=now_ms)
            effective_agents = tuple(agents) or tuple(
                agent for entry_phase, agent in self._entries if entry_phase == phase
            )
        if not effective_agents:
            return True
        return any(
            _project_eligibility(snapshot, phase=phase, agent=agent, now_ms=now_ms).is_eligible
            for agent in effective_agents
        )

    def registered_agents(self, phase: str) -> tuple[str, ...]:
        with self._lock:
            return tuple(agent for entry_phase, agent in self._entries if entry_phase == phase)

    def mark_unavailable(self, phase: str, agent: str, *, reason: str) -> None:
        with self._lock:
            entry = self._entries.get((phase, agent))
            if entry is None:
                entry = _LedgerEntry()
                self._entries[(phase, agent)] = entry
            entry.unavailable_reason = reason
            entry.cooldown_until_ms = None
            entry.cooldown_reason = None

    def earliest_wait_ms(self, phase: str, agents: Sequence[str]) -> int | None:
        if not agents:
            return 0
        wait = self.eligibility_wait(phase, agents)
        if wait.kind is WaitKind.AVAILABLE_NOW:
            return 0
        if wait.kind is WaitKind.COOLING_DOWN:
            return wait.wait_ms
        return None

    def all_exhausted(self, phase: str, agents: Sequence[str]) -> bool:
        if not agents:
            return False
        return all(status.is_spent for _agent, status in self.eligibility(phase, agents))

    def requires_intervention(
        self,
        phase: str | None = None,
        agents: Sequence[str] = (),
        snapshot: Mapping[str, object] | None = None,
    ) -> bool | None:
        with self._lock:
            now_ms = int(self._clock.monotonic() * 1000)
            snap = snapshot if snapshot is not None else self._snapshot_locked(now_ms=now_ms)
            if agents and phase is not None:
                targets = [(phase, agent) for agent in agents]
                require_all = True
            else:
                require_all = False
                raw_entries = snap.get("entries") if isinstance(snap, Mapping) else None
                targets = []
                if isinstance(raw_entries, Mapping):
                    for key in raw_entries:
                        if isinstance(key, str) and ":" in key:
                            p, a = key.split(":", 1)
                            if phase is None or p == phase:
                                targets.append((p, a))
                if not targets and self._entries:
                    targets = [
                        (p, a) for (p, a) in self._entries if phase is None or p == phase
                    ] or list(self._entries.keys())
            if not targets:
                return None
            statuses = [
                _project_eligibility(snap, phase=p, agent=a, now_ms=now_ms) for p, a in targets
            ]
            if any(status.is_cooldown for status in statuses):
                return False
            spent_condition = (
                all(status.is_spent for status in statuses)
                if require_all
                else any(status.is_spent for status in statuses)
            )
            if spent_condition:
                return True
            return None

    def failure_reason(self, phase: str, agent: str) -> str | None:
        st = self.status(phase, agent)
        if isinstance(st, (Cooldown, Spent, Unavailable)):
            return st.reason
        return None

    def record(self, fact: AttemptFact) -> AttemptOutcome:
        with self._lock:
            key = (fact.phase, fact.agent)
            now_ms = int(self._clock.monotonic() * 1000)

            entry = self._entries.get(key)
            if entry is None:
                entry = _LedgerEntry()
                self._entries[key] = entry

            self._sync_cooldown_expiry(entry, now_ms)

            if fact.artifact_valid:
                entry.consecutive_unproductive = 0
                entry.total_unproductive = 0
                entry.cooldown_until_ms = None
                entry.cooldown_reason = None
                entry.cooldown_count = 0
                entry.is_spent = False
                entry.spent_reason = None
                entry.unavailable_reason = None
                return AttemptOutcome(
                    status=Eligible(),
                    consecutive_unproductive=0,
                    total_unproductive=0,
                )

            if fact.workspace_changed is True:
                entry.consecutive_unproductive = 0
                entry.total_unproductive += 1
            else:
                entry.consecutive_unproductive += 1
                entry.total_unproductive += 1

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

    def _snapshot_locked(self, *, now_ms: int) -> dict[str, object]:
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
                "unavailable_reason": entry.unavailable_reason,
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
            now_ms = int(self._clock.monotonic() * 1000)
            return self._snapshot_locked(now_ms=now_ms)

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
        agent: str = "claude",
        consecutive_limit: int = 3,
        total_limit: int = 9,
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
                    unavailable_reason_val = item.get("unavailable_reason")

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
                        unavailable_reason=str(unavailable_reason_val)
                        if isinstance(unavailable_reason_val, str)
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
    "Spent",
    "Unavailable",
    "WaitKind",
    "_LegacyGuardSource",
    "eligibility_wait_from_statuses",
    "migrate_legacy_guard_history",
    "project_chain_eligibility",
    "project_eligibility",
]
