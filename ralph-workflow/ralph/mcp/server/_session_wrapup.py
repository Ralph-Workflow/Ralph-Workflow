"""Graduated session soft wrap-up nag.

Once a single agent invocation passes the soft threshold, every MCP tool result
carries a wrap-up banner so the agent finishes up and calls ``declare_complete``
before the hard wall-clock force-cut (enforced separately by the idle watchdog's
``SESSION_CEILING_EXCEEDED``). This is the "nag, then cut" half of the session
ceiling: the watchdog kills a runaway, but the nag gives a well-behaved agent a
chance to land its work first.

The clock is injected so timing is deterministic in tests.
"""

from __future__ import annotations

from typing import Protocol

__all__ = ["SessionWrapupBudget", "wrapup_notice"]


class _Clock(Protocol):
    def monotonic(self) -> float: ...


def wrapup_notice(
    *,
    elapsed_seconds: float,
    soft_seconds: float | None,
    hard_seconds: float | None,
) -> str | None:
    """Return a wrap-up banner when past the soft threshold, else None."""
    if soft_seconds is None or elapsed_seconds < soft_seconds:
        return None
    if hard_seconds is not None:
        remaining_minutes = max(0, int((hard_seconds - elapsed_seconds) // 60))
        return (
            f"⚠️ ~{remaining_minutes} min of your time budget remain. Finish up and call"
            " declare_complete soon — remaining work will be force-stopped at the cap."
        )
    return (
        "⚠️ You are past your soft time budget. Finish up and call declare_complete soon."
    )


class SessionWrapupBudget:
    """Tracks invocation elapsed time and produces the wrap-up notice."""

    def __init__(
        self,
        clock: _Clock,
        *,
        soft_seconds: float | None,
        hard_seconds: float | None,
    ) -> None:
        self._clock = clock
        self._soft_seconds = soft_seconds
        self._hard_seconds = hard_seconds
        self._started_at = clock.monotonic()

    def notice(self) -> str | None:
        """Return the current wrap-up banner, or None if not yet past the soft threshold."""
        elapsed = self._clock.monotonic() - self._started_at
        return wrapup_notice(
            elapsed_seconds=elapsed,
            soft_seconds=self._soft_seconds,
            hard_seconds=self._hard_seconds,
        )
