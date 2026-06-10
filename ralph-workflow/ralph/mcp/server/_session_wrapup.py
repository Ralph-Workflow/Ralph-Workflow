"""Graduated session soft wrap-up nag.

Once a single agent invocation passes the soft threshold, every MCP tool result
carries a wrap-up banner so the agent finishes up and calls ``declare_complete``
before the hard wall-clock force-cut (enforced separately by the idle watchdog's
``SESSION_CEILING_EXCEEDED``). This is the "nag, then cut" half of the session
ceiling: the watchdog kills a runaway, but the nag gives a well-behaved agent a
chance to land its work first.

Per-invocation contract
-----------------------

``SessionWrapupBudget`` is owned by ONE agent invocation. The underlying
``_started_at`` clock must NOT carry over when a new attempt begins. The
following are equivalent (per the canonical AC-01..AC-05 contract documented in
``.agent/PLAN.md``):

- the orchestrator's ``effect_executor._run_attempt`` calls
  ``bridge.reset_session_budget()`` at the top of every attempt (the
  per-attempt boundary that ``_invoke_agent_with_recovery`` drives), which
  posts ``notifications/reset_wrapup`` over HTTP to the inner subprocess;
- the inner subprocess's :class:`McpServer` dispatches that method to
  :meth:`McpServer.reset_session_budget`, which creates a fresh
  ``SessionWrapupBudget(SystemClock(), ...)`` and replaces the existing
  ``_wrapup_provider`` in-place;
- a fresh command line to init the agent (operator-initiated restart) is
  treated the same way: a new process, a new budget, ``elapsed=0`` from the
  first tool result.

The clock is injected so timing is deterministic in tests; the production
reset path uses :class:`SystemClock` and the canonical ``MAX_SESSION_SECONDS``
and ``SESSION_SOFT_WRAPUP_SECONDS`` defaults from
:mod:`ralph.timeout_defaults`. Honouring ``RALPH_MAX_SESSION_SECONDS`` /
``RALPH_SESSION_SOFT_WRAPUP_SECONDS`` env vars in the in-process reset is
deliberately NOT done — those env vars are read by the standalone subprocess
path (``ralph.mcp.server.runtime._session_wrapup_provider``) and apply to
the freshly-spawned subprocess; the in-process reset uses the
``timeout_defaults`` constants so the API contract (one budget per invocation)
stays explicit.
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
    """Tracks invocation elapsed time and produces the wrap-up notice.

    Per-invocation ownership: a single ``SessionWrapupBudget`` instance is
    scoped to ONE agent invocation. Callers MUST NOT reuse a budget across
    attempts; instead, construct a fresh instance (e.g. via
    :meth:`McpServer.reset_session_budget`, which does exactly this) so the
    underlying ``_started_at`` clock does not carry over from a prior attempt.
    The 60-minute timing budget is a per-invocation soft timeout: a fresh
    command line to init the agent (operator-initiated restart) or a retry
    within ``effect_executor._invoke_agent_with_recovery`` is a fresh attempt
    with a fresh budget.
    """

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
