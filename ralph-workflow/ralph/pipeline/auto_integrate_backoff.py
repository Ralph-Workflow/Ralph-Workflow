
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


RETRY_BASE_DELAY_SECONDS = 0.5

RETRY_MAX_DELAY_SECONDS = 4.0

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
    remaining = deadline - now
    return 0.0 < remaining <= MAX_RETRY_DEADLINE_HORIZON_SECONDS


def _read_clock_seconds(clock: object) -> float:
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
    try:
        delay = compute_retry_delay(attempt, jitter=jitter)
        sleep(delay)
    except Exception as exc:
        logger.debug("auto_integrate: retry backoff failed (non-fatal): {}", exc)
