"""Invariant tests for the watchdog-kill -> resume boundary.

The chain is:

1. ``IdleWatchdog.evaluate()`` chooses a ``WatchdogFireReason``.
2. The reason is surfaced through ``IdleWatchdogKilledError.reason``.
3. ``_process_reader._is_resumable_fire_reason`` decides
   ``session_resume_safe``.
4. The recovery controller maps ``session_resume_safe=True`` +
   ``has_prior_session=True`` -> ``action='resume'``.

These tests pin that boundary as a single contract so a future refactor
that adds a new ``WatchdogFireReason`` member (or moves one between the
resumable/non-resumable sets) cannot silently regress resume-after-kill.
"""

from __future__ import annotations

import pytest

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.idle_watchdog.idle_watchdog import _EXPECTED_FIRE_REASONS
from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError
from ralph.agents.invoke._process_reader import (
    _RESUMABLE_FIRE_REASONS,
    _is_resumable_fire_reason,
)

_RESUMABLE_REASON_VALUES: frozenset[str] = frozenset(
    r.value for r in _RESUMABLE_FIRE_REASONS
)
_NON_RESUMABLE_REASON_VALUES: frozenset[str] = frozenset(
    {
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG.value,
        WatchdogFireReason.SESSION_CEILING_EXCEEDED.value,
        WatchdogFireReason.PROCESS_EXIT_HANG.value,
        WatchdogFireReason.DESCENDANT_HANG.value,
        WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER.value,
        WatchdogFireReason.STRICTLY_STUCK.value,
    }
)


def test_is_resumable_fire_reason_classifies_known_reasons() -> None:
    """``_is_resumable_fire_reason`` returns True for the canonical in-set
    and False for every known non-resumable reason.
    """
    for reason in _RESUMABLE_FIRE_REASONS:
        assert _is_resumable_fire_reason(reason) is True, (
            f"{reason!r} MUST be resumable"
        )

    for reason in (
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
        WatchdogFireReason.SESSION_CEILING_EXCEEDED,
        WatchdogFireReason.PROCESS_EXIT_HANG,
        WatchdogFireReason.DESCENDANT_HANG,
        WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER,
    ):
        assert _is_resumable_fire_reason(reason) is False, (
            f"{reason!r} MUST NOT be resumable"
        )


@pytest.mark.parametrize("reason_value", sorted(_EXPECTED_FIRE_REASONS))
def test_expected_fire_reasons_partitioned_into_resumable_or_excluded(
    reason_value: str,
) -> None:
    """Every reason in the import-time ``_EXPECTED_FIRE_REASONS`` lock is
    either resumable or explicitly documented as non-resumable.

    If a future PR adds a ``WatchdogFireReason`` member without updating
    the resume contract, this test fails.
    """
    assert (
        reason_value in _RESUMABLE_REASON_VALUES
        or reason_value in _NON_RESUMABLE_REASON_VALUES
    ), (
        f"{reason_value!r} is neither resumable nor in the documented"
        f" non-resumable exclusion set; update the resume contract"
    )


@pytest.mark.parametrize("reason", sorted(WatchdogFireReason, key=str))
def test_idle_watchdog_killed_error_reason_round_trips_through_resumable_helper(
    reason: WatchdogFireReason,
) -> None:
    """A kill exception carrying any ``WatchdogFireReason`` is classified
    consistently by ``_is_resumable_fire_reason``.

    This round-trips the boundary: ``reason.value`` flows from the
    exception to the helper and back to a ``WatchdogFireReason`` enum
    member.
    """
    exc = IdleWatchdogKilledError(
        reason=reason.value,
        signal=15,
        evidence_summary="test boundary",
        child_alive=False,
    )
    recovered = WatchdogFireReason(exc.reason)
    expected = reason in _RESUMABLE_FIRE_REASONS
    assert _is_resumable_fire_reason(recovered) is expected, (
        f"{reason!r}: round-trip resumability mismatch; expected {expected}"
    )
