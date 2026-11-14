from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

LEGACY_DEFAULT_AGENT = "claude"
_LEGACY_MALFORMED_REASON = "legacy_guard_malformed"

_DEFAULT_CONSECUTIVE_LIMIT = 3
_DEFAULT_TOTAL_LIMIT = 9
_DEFAULT_BASE_COOLDOWN_SECONDS = 60.0
_DEFAULT_MAX_COOLDOWN_SECONDS = 3600.0
_DEFAULT_BACKOFF_FACTOR = 2.0


@dataclass(frozen=True)
class _LegacyGuardSource:
    payload: object
    phase: str
    agent: str | None = None
    candidate_agents: tuple[str, ...] = ()
    fail_closed_reason: str | None = None


def _counter_value(raw: object) -> int:
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
    if field == "streak":
        return _readable_streak(raw) is not None
    if field == "max_identical":
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            return False
        return _counter_value(raw) > 0
    return raw is None or isinstance(raw, bool)


def _readable_legacy_payload(payload: object) -> Mapping[str, object] | None:
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
        "unavailable_reason": None,
    }


def _entry_strictness(
    entry: Mapping[str, object],
    *,
    consecutive_limit: int,
    total_limit: int,
) -> tuple[int, int, int, int]:
    spent = 0 if bool(entry.get("is_spent")) else 1
    consecutive = _counter_value(entry.get("consecutive_unproductive"))
    total = _counter_value(entry.get("total_unproductive"))
    remaining = max(0, min(consecutive_limit - consecutive, total_limit - total))
    return (spent, remaining, -total, -consecutive)


def _snapshot_limits(
    snapshot: Mapping[str, object] | None,
) -> tuple[int, int, float, float, float]:
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


def translate_legacy_entry(
    guard_data: Mapping[str, object],
    phase: str,
    agent: str,
    *,
    consecutive_limit: int = 3,
    total_limit: int = 9,
) -> dict[str, object]:
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
        "unavailable_reason": None,
    }


def _translate_legacy_source(
    source: _LegacyGuardSource,
    phase: str,
    agent: str,
    *,
    consecutive_limit: int,
    total_limit: int,
) -> dict[str, object]:
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
    return translate_legacy_entry(
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
    "LEGACY_DEFAULT_AGENT",
    "_LegacyGuardSource",
    "migrate_legacy_guard_history",
    "translate_legacy_entry",
]
