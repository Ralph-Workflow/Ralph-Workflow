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

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable

#: First backoff step. Long enough that two agents which collided are
#: unlikely to re-collide, short enough that a seam does not visibly
#: stall a run.
RETRY_BASE_DELAY_SECONDS = 0.5

#: Ceiling on that backoff. The budget is three attempts inside ONE
#: seam; the next commit or phase boundary is the real long-range retry,
#: so waiting longer here buys nothing.
RETRY_MAX_DELAY_SECONDS = 4.0

__all__ = [
    "RETRY_BASE_DELAY_SECONDS",
    "RETRY_MAX_DELAY_SECONDS",
    "wait_before_retry",
]


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
        step: float = RETRY_BASE_DELAY_SECONDS * 2.0 ** (attempt - 1)
        spread: float = 0.5 + 0.5 * jitter()
        delay: float = min(step, RETRY_MAX_DELAY_SECONDS) * spread
        sleep(delay)
    except Exception as exc:
        logger.debug("auto_integrate: retry backoff failed (non-fatal): {}", exc)
