from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.checked_accessors import as_int, as_mapping
from ralph.recovery.agent_unavailability_tracker import (
    DEFAULT_LEGACY_BACKOFF_MS,
    DEFAULT_LEGACY_MAX_BACKOFF_MS,
    UnavailabilityEntry,
)
from ralph.recovery.unavailability_reason import (
    _UNIVERSAL_COOLDOWN_CAP_MS,
    DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    UnavailabilityReason,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.agents.clock import Clock


def restored_unavailability_entries(
    timeouts: Mapping[str, int],
    reasons: Mapping[str, object],
    backoff_attempts: Mapping[str, int],
) -> dict[str, UnavailabilityEntry]:
    entries: dict[str, UnavailabilityEntry] = {}
    for agent, until_ms in timeouts.items():
        raw_reason = reasons.get(agent)
        if not isinstance(raw_reason, str):
            continue
        try:
            reason = UnavailabilityReason(raw_reason)
        except ValueError:
            continue
        policy = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY.get(reason)
        if policy is None:
            continue
        entries[agent] = UnavailabilityEntry(
            unavailable_until_ms=until_ms,
            reason=reason,
            attempt=max(0, backoff_attempts.get(agent, 1) - 1),
            base_backoff_ms=policy.base_backoff_ms,
            max_backoff_ms=policy.max_backoff_ms,
        )
    return entries


_COOLDOWN_EXPIRY_KEY = "unavailable_until_epoch_ms"
_COOLDOWN_REASON_KEY = "unavailability_reasons"
_COOLDOWN_ATTEMPTS_KEY = "backoff_attempts"


@dataclass(frozen=True)
class RestoredCooldowns:
    entries: dict[str, UnavailabilityEntry]
    backoff_attempts: dict[str, int]


def persisted_cooldowns(
    tracker_snapshot: Mapping[str, Mapping[str, object]],
    *,
    now_ms: int,
) -> dict[str, object] | None:
    timeouts = tracker_snapshot.get("unavailable_timeouts", {})
    expiries = tracker_snapshot.get(_COOLDOWN_EXPIRY_KEY, {})
    reasons = tracker_snapshot.get(_COOLDOWN_REASON_KEY, {})
    attempts = dict(tracker_snapshot.get(_COOLDOWN_ATTEMPTS_KEY, {}))
    active = [
        agent
        for agent, until_ms in timeouts.items()
        if isinstance(until_ms, int) and until_ms > now_ms and agent in expiries
    ]
    if not active and not attempts:
        return None
    return {
        _COOLDOWN_EXPIRY_KEY: {agent: expiries[agent] for agent in active},
        _COOLDOWN_REASON_KEY: {agent: reasons.get(agent) for agent in active},
        _COOLDOWN_ATTEMPTS_KEY: attempts,
    }


def _restored_entry(until_ms: int, raw_reason: object, attempts: int) -> UnavailabilityEntry:
    reason: UnavailabilityReason | None = None
    if isinstance(raw_reason, str):
        try:
            reason = UnavailabilityReason(raw_reason)
        except ValueError:
            reason = None
    policy = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY.get(reason) if reason is not None else None
    return UnavailabilityEntry(
        unavailable_until_ms=until_ms,
        reason=reason,
        attempt=max(0, attempts - 1),
        base_backoff_ms=policy.base_backoff_ms if policy is not None else DEFAULT_LEGACY_BACKOFF_MS,
        max_backoff_ms=(
            policy.max_backoff_ms if policy is not None else DEFAULT_LEGACY_MAX_BACKOFF_MS
        ),
    )


def restored_cooldowns(
    snapshot: Mapping[str, object] | None,
    *,
    clock: Clock,
) -> RestoredCooldowns:
    if not snapshot:
        return RestoredCooldowns(entries={}, backoff_attempts={})
    attempts = {
        agent: as_int(value, field=f"{_COOLDOWN_ATTEMPTS_KEY}[{agent}]")
        for agent, value in as_mapping(
            snapshot.get(_COOLDOWN_ATTEMPTS_KEY, {}), field=_COOLDOWN_ATTEMPTS_KEY
        ).items()
    }
    reasons = as_mapping(snapshot.get(_COOLDOWN_REASON_KEY, {}), field=_COOLDOWN_REASON_KEY)
    expiries = {
        agent: as_int(value, field=f"{_COOLDOWN_EXPIRY_KEY}[{agent}]")
        for agent, value in as_mapping(
            snapshot.get(_COOLDOWN_EXPIRY_KEY, {}), field=_COOLDOWN_EXPIRY_KEY
        ).items()
    }
    now_ms = int(clock.monotonic() * 1000)
    now_epoch_ms = int(clock.epoch() * 1000)
    remaining = {
        agent: min(expiry_ms - now_epoch_ms, _UNIVERSAL_COOLDOWN_CAP_MS)
        for agent, expiry_ms in expiries.items()
    }
    return RestoredCooldowns(
        entries={
            agent: _restored_entry(now_ms + left_ms, reasons.get(agent), attempts.get(agent, 0))
            for agent, left_ms in remaining.items()
            if left_ms > 0
        },
        backoff_attempts=attempts,
    )


def _reason_cap_ms(raw_reason: object) -> int:
    if isinstance(raw_reason, str):
        try:
            policy = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY.get(UnavailabilityReason(raw_reason))
        except ValueError:
            policy = None
        if policy is not None:
            return min(policy.max_backoff_ms, _UNIVERSAL_COOLDOWN_CAP_MS)
    return _UNIVERSAL_COOLDOWN_CAP_MS


def rebased_unavailable_timeouts(
    timeouts: Mapping[str, int],
    epoch_deadlines: Mapping[str, int],
    reasons: Mapping[str, object],
    *,
    clock: Clock,
) -> dict[str, int]:
    now_ms = int(clock.monotonic() * 1000)
    now_epoch_ms = int(clock.epoch() * 1000)
    rebased: dict[str, int] = {}
    for agent, until_ms in timeouts.items():
        epoch_deadline = epoch_deadlines.get(agent)
        remaining_ms = (
            epoch_deadline - now_epoch_ms if epoch_deadline is not None else until_ms - now_ms
        )
        rebased[agent] = now_ms + min(max(0, remaining_ms), _reason_cap_ms(reasons.get(agent)))
    return rebased
