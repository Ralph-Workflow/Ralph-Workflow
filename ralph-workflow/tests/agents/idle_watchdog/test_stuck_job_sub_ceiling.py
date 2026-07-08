"""Regression tests for the ``stuck_job_sub_ceiling_seconds`` TimeoutPolicy field.

The PROMPT trace showed the watchdog's cumulative ``WAITING_ON_CHILD`` time
climbing to ~2365s without the gate firing on a stuck-but-alive child.
Root cause: ``classify_stuck`` never returned ``STUCK`` while the corroborator
reported an ``OS_DESCENDANT_ONLY_STALE_PROGRESS`` (or any stale alive_by),
so ``_gate_fire`` kept returning ``CONTINUE`` and the deferred-firing path
ran forever.

The fix: a NEW sub-ceiling ``stuck_job_sub_ceiling_seconds`` (default 600s)
configured via ``TimeoutPolicy``. When the cumulative ``WAITING_ON_CHILD`` time
exceeds the sub-ceiling AND the corroborator reports a STALE alive_by
(``OS_DESCENDANT_ONLY_STALE_PROGRESS`` / ``CPU_IDLE_WHILE_ALIVE`` /
``LOG_STALE_WHILE_ALIVE`` / ``STALE_LABEL_ONLY``) AND ``scoped_child_active``
is True, the watchdog MUST fire ``CHILDREN_PERSIST_TOO_LONG``.

This test pins the contract end-to-end via the public ``evaluate()`` API:

  * ``test_stuck_job_sub_ceiling_fires_at_600s_when_alive_by_is_stale``:
    drive a stale corroborator into the waiting branch and advance past the
    sub-ceiling; assert ``FIRE`` with ``CHILDREN_PERSIST_TOO_LONG``.

  * ``test_stuck_job_sub_ceiling_does_not_fire_when_alive_by_is_fresh``:
    a productive live child (``FRESH_PROGRESS``) MUST NOT trip the
    sub-ceiling; the deferred waiting branch continues normally.

  * ``test_stuck_job_sub_ceiling_disabled_when_none``: a None
    sub-ceiling preserves the legacy behavior (the legacy
    ``max_waiting_on_child_no_progress_seconds`` ceiling is the
    only stuck-job detector).

  * ``test_stuck_job_sub_ceiling_validated_positive_and_bounded``: the
    field is validated in ``__post_init__``: must be > 0 and <=
    ``max_waiting_on_child_seconds`` when set.

All tests use ``FakeClock`` and the public ``evaluate()`` API; no real
subprocess, no real sleep, no real network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    CorroborationSnapshot,
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.process.child_liveness import AliveBy
from ralph.timeout_defaults import STUCK_JOB_SUB_CEILING_SECONDS


def _make_policy(
    *,
    stuck_job_sub_ceiling_seconds: float | None = 600.0,
    max_waiting_on_child_seconds: float = 1800.0,
    max_waiting_on_child_no_progress_seconds: float | None = 1800.0,
    idle_timeout_seconds: float = 200.0,
    drain_window_seconds: float = 0.0,
    os_descendant_only_ceiling_seconds: float | None = None,
    os_descendant_only_suspect_seconds: float | None = None,
    no_progress_quiet_heartbeat_ceiling_seconds: float | None = None,
) -> TimeoutPolicy:
    return TimeoutPolicy(
        idle_timeout_seconds=idle_timeout_seconds,
        drain_window_seconds=drain_window_seconds,
        max_waiting_on_child_seconds=max_waiting_on_child_seconds,
        max_waiting_on_child_no_progress_seconds=max_waiting_on_child_no_progress_seconds,
        no_progress_quiet_seconds=None,
        no_progress_quiet_minimum_invocation_seconds=None,
        no_progress_quiet_heartbeat_ceiling_seconds=no_progress_quiet_heartbeat_ceiling_seconds,
        suspect_waiting_on_child_seconds=None,
        os_descendant_only_ceiling_seconds=os_descendant_only_ceiling_seconds,
        os_descendant_only_suspect_seconds=os_descendant_only_suspect_seconds,
        no_output_at_start_seconds=None,
        activity_evidence_ttl_seconds=180.0,
        silent_subagent_seconds=None,
        stuck_job_sub_ceiling_seconds=stuck_job_sub_ceiling_seconds,
    )


def _waiting_on_child() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def _make_stuck_corroborator(
    alive_by: AliveBy = AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
) -> Callable[[], CorroborationSnapshot]:
    """Corroborator that always reports a stale alive_by with a scoped child active."""

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=alive_by,
            scoped_child_active=True,
            oldest_child_seconds=200.0,
        )

    return _corroborator


def test_stuck_job_sub_ceiling_fires_at_600s_when_alive_by_is_stale() -> None:
    """The sub-ceiling MUST fire ``CHILDREN_PERSIST_TOO_LONG`` when:

      * cumulative ``WAITING_ON_CHILD`` time >= ``stuck_job_sub_ceiling_seconds``
      * corroborator reports a stale ``AliveBy``
      * ``scoped_child_active`` is True

    Drives the watchdog through a full waiting run with the stale
    corroborator; the sub-ceiling fires at 600s well before the 1800s
    cumulative ceiling.
    """
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        _make_policy(stuck_job_sub_ceiling_seconds=600.0),
        clock,
        corroborator=_make_stuck_corroborator(),
    )
    watchdog.record_invocation_start()

    # Advance past idle_timeout so the waiting branch is reachable.
    clock.advance(201.0)
    # Enter the waiting branch via the public API.
    verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)
    assert verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"first evaluate MUST enter the waiting branch; got {verdict!r}"
    )

    # Advance 600s inside the waiting branch. With
    # drain_window_seconds=0 the cumulative waiting time ticks up to
    # 600s at this point and the sub-ceiling trips.
    clock.advance(600.0)
    verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)

    assert verdict == WatchdogVerdict.FIRE, (
        f"stuck_job_sub_ceiling MUST fire at 600s with stale alive_by; got verdict={verdict!r}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG, (
        f"expected CHILDREN_PERSIST_TOO_LONG; got {watchdog.last_fire_reason!r}"
    )


@pytest.mark.parametrize(
    "alive_by",
    (
        AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
        AliveBy.CPU_IDLE_WHILE_ALIVE,
        AliveBy.LOG_STALE_WHILE_ALIVE,
        AliveBy.STALE_LABEL_ONLY,
    ),
)
def test_stuck_job_sub_ceiling_fires_for_every_stale_alive_by(alive_by: AliveBy) -> None:
    """The sub-ceiling MUST trip for every stale alive_by value.

    The fix must not single out one stale value (e.g.
    ``OS_DESCENDANT_ONLY_STALE_PROGRESS`` only); all four stale values
    in ``_NON_PROGRESS_ALIVE_BY_VALUES`` trip the sub-ceiling
    consistently.
    """
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        _make_policy(stuck_job_sub_ceiling_seconds=600.0),
        clock,
        corroborator=_make_stuck_corroborator(alive_by),
    )
    watchdog.record_invocation_start()

    clock.advance(201.0)
    verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)
    assert verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"first evaluate MUST enter the waiting branch; got {verdict!r}"
    )

    clock.advance(600.0)
    verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)

    assert verdict == WatchdogVerdict.FIRE, (
        f"stuck_job_sub_ceiling MUST fire for alive_by={alive_by.value!r}; got verdict={verdict!r}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


@pytest.mark.parametrize(
    "alive_by",
    (AliveBy.FRESH_PROGRESS, AliveBy.FRESH_HEARTBEAT_ONLY),
)
def test_stuck_job_sub_ceiling_does_not_fire_when_alive_by_is_fresh(
    alive_by: AliveBy,
) -> None:
    """The sub-ceiling MUST NOT trip for FRESH alive_by values.

    A productive live child agent (``FRESH_PROGRESS`` /
    ``FRESH_HEARTBEAT_ONLY``) is by definition NOT a stuck job; the
    sub-ceiling is exclusively the stuck-but-alive detector.
    """
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        _make_policy(stuck_job_sub_ceiling_seconds=600.0),
        clock,
        corroborator=_make_stuck_corroborator(alive_by),
    )
    watchdog.record_invocation_start()

    clock.advance(201.0)
    verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)
    assert verdict == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(600.0)
    verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)

    assert verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"stuck_job_sub_ceiling MUST NOT fire for FRESH alive_by="
        f"{alive_by.value!r}; got verdict={verdict!r}"
    )
    assert watchdog.last_fire_reason is None, (
        f"stuck_job_sub_ceiling MUST NOT fire for FRESH alive_by="
        f"{alive_by.value!r}; got last_fire_reason="
        f"{watchdog.last_fire_reason!r}"
    )


def test_stuck_job_sub_ceiling_disabled_when_none() -> None:
    """A None sub-ceiling preserves the legacy behavior.

    When ``stuck_job_sub_ceiling_seconds=None``, the sub-ceiling is
    disabled. The waiting branch continues to use
    ``max_waiting_on_child_no_progress_seconds`` as the only
    stuck-job detector (the legacy 600s ceiling).
    """
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        _make_policy(
            stuck_job_sub_ceiling_seconds=None,
            max_waiting_on_child_no_progress_seconds=1800.0,
        ),
        clock,
        corroborator=_make_stuck_corroborator(),
    )
    watchdog.record_invocation_start()

    clock.advance(201.0)
    verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)
    assert verdict == WatchdogVerdict.WAITING_ON_CHILD

    # Advance to 700s (past the sub-ceiling that is now disabled).
    clock.advance(499.0)
    verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)

    # The sub-ceiling is disabled, so the gate does NOT fire at 700s.
    # Cumulative waiting time is 700s, well under the 1800s
    # max_waiting_on_child_no_progress_seconds ceiling.
    assert verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"with sub-ceiling disabled, the gate MUST NOT fire at 700s; got verdict={verdict!r}"
    )
    assert watchdog.last_fire_reason is None


def test_timeout_policy_default_stuck_job_sub_ceiling_matches_constant() -> None:
    """Default direct ``TimeoutPolicy`` callers get the 600s sub-ceiling."""
    policy = TimeoutPolicy(idle_timeout_seconds=200.0)

    assert policy.stuck_job_sub_ceiling_seconds == STUCK_JOB_SUB_CEILING_SECONDS
    assert policy.stuck_job_sub_ceiling_seconds == 600.0


def test_stuck_job_sub_ceiling_validated_positive_and_bounded() -> None:
    """The field MUST be validated in ``__post_init__``.

    Mirrors the other TimeoutPolicy validators:
      - ``<= 0`` raises ``ValueError``
      - ``> max_waiting_on_child_seconds`` raises ``ValueError``
    """
    # Negative value MUST be rejected.
    with pytest.raises(ValueError, match="stuck_job_sub_ceiling_seconds must be positive"):
        TimeoutPolicy(
            idle_timeout_seconds=200.0,
            suspect_waiting_on_child_seconds=None,
            stuck_job_sub_ceiling_seconds=-1.0,
        )
    # Zero MUST be rejected.
    with pytest.raises(ValueError, match="stuck_job_sub_ceiling_seconds must be positive"):
        TimeoutPolicy(
            idle_timeout_seconds=200.0,
            suspect_waiting_on_child_seconds=None,
            stuck_job_sub_ceiling_seconds=0.0,
        )
    # Greater than max_waiting_on_child_seconds MUST be rejected.
    with pytest.raises(
        ValueError,
        match="stuck_job_sub_ceiling_seconds must be <= max_waiting_on_child_seconds",
    ):
        TimeoutPolicy(
            idle_timeout_seconds=200.0,
            suspect_waiting_on_child_seconds=None,
            max_waiting_on_child_seconds=600.0,
            stuck_job_sub_ceiling_seconds=700.0,
        )
    # A valid value MUST construct successfully.
    policy = TimeoutPolicy(
        idle_timeout_seconds=200.0,
        stuck_job_sub_ceiling_seconds=600.0,
    )
    assert policy.stuck_job_sub_ceiling_seconds == 600.0


def test_stuck_job_sub_ceiling_fires_when_scoped_child_inactive() -> None:
    """When ``scoped_child_active`` is False the sub-ceiling MUST NOT fire.

    The sub-ceiling is the stuck-but-alive detector; if no scoped
    child is active, the standard ``max_waiting_on_child_seconds``
    cumulative ceiling is the correct upper bound.
    """

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=False,
            oldest_child_seconds=200.0,
        )

    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        _make_policy(stuck_job_sub_ceiling_seconds=600.0),
        clock,
        corroborator=_corroborator,
    )
    watchdog.record_invocation_start()

    clock.advance(201.0)
    verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)
    assert verdict == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(600.0)
    verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)

    # No scoped child active -> the sub-ceiling MUST NOT trip.
    # The cumulative time is 600s, well under the 1800s
    # max_waiting_on_child_seconds ceiling.
    assert verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"sub-ceiling MUST NOT fire when scoped_child_active=False; got verdict={verdict!r}"
    )
    assert watchdog.last_fire_reason is None
