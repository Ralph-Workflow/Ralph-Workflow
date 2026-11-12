from __future__ import annotations

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.recovery.attempt_facts import TerminationIssuer

_SUPERVISOR_IDLE_FIRE_REASONS: frozenset[WatchdogFireReason] = frozenset(
    {
        WatchdogFireReason.CONFLICT_INACTIVITY,
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
        WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER,
        WatchdogFireReason.NO_OUTPUT_AT_START,
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        WatchdogFireReason.NO_PROGRESS_QUIET,
        WatchdogFireReason.REPEATED_ERROR_LOOP,
        WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL,
        WatchdogFireReason.STALLED_AFTER_TOOL_RESULT,
        WatchdogFireReason.STRICTLY_STUCK,
    }
)

_RECOVERY_TIMEOUT_FIRE_REASONS: frozenset[WatchdogFireReason] = frozenset(
    {
        WatchdogFireReason.SESSION_CEILING_EXCEEDED,
    }
)

_POST_EXIT_WATCHDOG_FIRE_REASONS: frozenset[WatchdogFireReason] = frozenset(
    {
        WatchdogFireReason.DESCENDANT_HANG,
        WatchdogFireReason.PROCESS_EXIT_HANG,
    }
)


def fire_reason_from_value(value: str) -> WatchdogFireReason | None:
    try:
        return WatchdogFireReason(value)
    except ValueError:
        return None


def termination_issuer_for_fire_reason(
    reason: WatchdogFireReason | str | None,
) -> TerminationIssuer:
    if reason is None:
        return TerminationIssuer.UNKNOWN
    value = reason.value if isinstance(reason, WatchdogFireReason) else str(reason)
    fire_reason = fire_reason_from_value(value)
    if fire_reason is None:
        return TerminationIssuer.UNKNOWN
    if fire_reason in _SUPERVISOR_IDLE_FIRE_REASONS:
        return TerminationIssuer.SUPERVISOR_IDLE
    if fire_reason in _POST_EXIT_WATCHDOG_FIRE_REASONS:
        return TerminationIssuer.POST_EXIT_WATCHDOG
    if fire_reason in _RECOVERY_TIMEOUT_FIRE_REASONS:
        return TerminationIssuer.RECOVERY_TIMEOUT
    return TerminationIssuer.UNKNOWN
