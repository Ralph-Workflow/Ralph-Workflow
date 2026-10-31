"""Bounded jittered wait between auto-integration landing retries.

Separated from :mod:`ralph.pipeline.auto_integrate` so that module stays
under the repo-structure ``_MAX_FILE_LINES`` cap, the same reason
:mod:`ralph.pipeline.auto_integrate_recovery` and
:mod:`ralph.pipeline.auto_integrate_refresh` live beside it.

Why a wait exists at all. The landing retry loop re-runs when the
fast-forward lost a compare-and-swap, and a lost CAS means ANOTHER agent
just won the race for the same mainline ref. Retrying immediately means
every loser re-rebases and re-races in lockstep, spends the whole
three-attempt budget in milliseconds, and starves the slowest agent off
the target until its next seam -- the opposite of the fleet
synchronisation auto-integration exists to provide.

Both the clock and the randomness are INJECTED rather than imported
here. ``ralph.testing.audit_test_policy`` forbids ``time.sleep`` in the
deterministic suite and the per-test cap is one second, so a real
backoff could not be proven inside the gate at all; tests pass a
recording ``sleep`` and a constant ``jitter`` and assert the schedule
without ever waiting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable


@runtime_checkable
class _SupportsMonotonic(Protocol):
    def monotonic(self) -> float: ...


@runtime_checkable
class _SupportsTime(Protocol):
    def time(self) -> float: ...


@runtime_checkable
class _SupportsCallSeconds(Protocol):
    def __call__(self) -> float: ...


#: First backoff step. Long enough that two agents which collided are
#: unlikely to re-collide, short enough that a seam does not visibly
#: stall a run.
RETRY_BASE_DELAY_SECONDS = 0.5

#: Ceiling on that backoff. The budget is three attempts inside ONE
#: seam; the next commit or phase boundary is the real long-range retry,
#: so waiting longer here buys nothing.
RETRY_MAX_DELAY_SECONDS = 4.0

#: Longest bounded-retry wait any writer may arm: one hour, the default
#: ceiling of an agent cooldown (``AgentFailureLedger``), and far above
#: :data:`RETRY_MAX_DELAY_SECONDS`. ``RebaseState.retry_deadline`` is a
#: MONOTONIC instant persisted in the checkpoint. After a reboot the
#: monotonic clock restarts near zero, so a deadline written during the
#: previous boot lies about the previous uptime in the future. Writers
#: never arm a deadline beyond this horizon, so a reader that finds one
#: further away knows it came from another clock origin and treats it as
#: expired (:func:`retry_deadline_is_live`).
MAX_RETRY_DEADLINE_HORIZON_SECONDS = 3600.0

__all__ = [
    "MAX_RETRY_DEADLINE_HORIZON_SECONDS",
    "RETRY_BASE_DELAY_SECONDS",
    "RETRY_MAX_DELAY_SECONDS",
    "compute_retry_deadline",
    "compute_retry_delay",
    "retry_deadline_is_live",
    "wait_before_retry",
]


def retry_deadline_is_live(deadline: float, now: float) -> bool:
    """Whether a persisted retry ``deadline`` still authorises waiting at ``now``.

    Live means strictly in the future and no further away than
    :data:`MAX_RETRY_DEADLINE_HORIZON_SECONDS`. A deadline beyond the
    horizon cannot have been armed on this clock (no writer arms one that
    far out); it is a monotonic reading from a previous boot and is stale.
    """
    remaining = deadline - now
    return 0.0 < remaining <= MAX_RETRY_DEADLINE_HORIZON_SECONDS


def _read_clock_seconds(clock: object) -> float:
    """Read current logical seconds from an injected clock abstraction."""
    if clock is None:
        raise TypeError("clock must not be None; provide an injected clock abstraction")
    if isinstance(clock, _SupportsMonotonic):
        try:
            return float(clock.monotonic())
        except (TypeError, ValueError) as exc:
            raise TypeError(f"clock.monotonic() failed to return numeric seconds: {exc}") from exc
    if isinstance(clock, _SupportsTime):
        try:
            return float(clock.time())
        except (TypeError, ValueError) as exc:
            raise TypeError(f"clock.time() failed to return numeric seconds: {exc}") from exc
    if isinstance(clock, _SupportsCallSeconds):
        try:
            return float(clock())
        except (TypeError, ValueError) as exc:
            raise TypeError(f"clock callable failed to return numeric seconds: {exc}") from exc
    raise TypeError(
        f"Injected clock {type(clock).__name__!r} must be callable or provide .monotonic() or .time()"
    )


def compute_retry_delay(
    attempt: int,
    *,
    jitter: Callable[[], float] | None = None,
) -> float:
    """Compute the bounded jittered retry delay in seconds for attempt ``attempt``."""
    if attempt < 1:
        return 0.0
    step: float = RETRY_BASE_DELAY_SECONDS * 2.0 ** (attempt - 1)
    jitter_val = jitter() if jitter is not None else 1.0
    spread: float = 0.5 + 0.5 * jitter_val
    return min(step, RETRY_MAX_DELAY_SECONDS) * spread


def compute_retry_deadline(
    attempt: int,
    *,
    clock: object,
    cooldown_seconds: float | None = None,
    jitter: Callable[[], float] | None = None,
) -> float:
    """Compute a durable deadline from an injected clock abstraction.

    Respects clock boundaries: only queries the injected clock, never
    falls back to direct time.monotonic / time.time calls in decision code.
    The delay is capped at :data:`MAX_RETRY_DEADLINE_HORIZON_SECONDS`, the
    horizon readers accept.
    """
    now = _read_clock_seconds(clock)
    delay = (
        cooldown_seconds
        if cooldown_seconds is not None
        else compute_retry_delay(attempt, jitter=jitter)
    )
    return now + min(delay, MAX_RETRY_DEADLINE_HORIZON_SECONDS)


def wait_before_retry(
    attempt: int,
    *,
    sleep: Callable[[float], None],
    jitter: Callable[[], float],
) -> None:
    """Pause before landing attempt ``attempt``.

    Args:
        attempt: 0-based attempt index. The caller only invokes this for
            ``attempt >= 1``, so the first attempt never waits.
        sleep: Wait seam, ``time.sleep`` in production.
        jitter: Uniform ``[0, 1)`` source, ``random.random`` in
            production.

    The delay is exponential with FULL jitter -- drawn from half to all
    of the exponential step -- because a fixed backoff would simply
    re-synchronise the agents it had just separated.

    Never raises. An exception out of either injected seam is swallowed,
    because the surrounding contract is that the integration step never
    raises into the run; a failed wait must cost at most a lost delay.
    """
    try:
        delay = compute_retry_delay(attempt, jitter=jitter)
        sleep(delay)
    except Exception as exc:
        logger.debug("auto_integrate: retry backoff failed (non-fatal): {}", exc)
