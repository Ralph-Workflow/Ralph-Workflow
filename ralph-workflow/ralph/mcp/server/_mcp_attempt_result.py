"""RestartAttemptResult — typed restart attempt outcome (C2 contract).

Only :attr:`RestartAttemptResult.BUDGET_EXHAUSTED` is terminal; it raises
:class:`McpServerError` to the supervisor and is re-raised from
``McpSupervisor.__exit__``. All other outcomes are non-terminal while the
rolling budget remains; the next health check tries again after the
cooldown. This is the C2 fix: one failed ``restart_fn`` call does NOT
end the agent run anymore.

The class deliberately uses string constants (not :class:`enum.Enum`)
so existing string-returning call sites that already compare to
``"ok"`` / ``"window_drain"`` / ``"absolute_cap"`` (see
:func:`ralph.mcp.server._mcp_restart_policy.next_budget_decision`)
share the same vocabulary; consumers can ``is``-compare on the
class itself without importing :mod:`enum`.
"""

from __future__ import annotations


class RestartAttemptResult:
    """Closed enum of restart attempt outcomes (C2 contract)."""

    #: The rolling-window or absolute budget is exhausted. The bridge
    #: raises :class:`McpServerError` to the supervisor (terminal).
    BUDGET_EXHAUSTED = "budget_exhausted"
    #: A single ``restart_fn`` call failed; the bridge keeps the old
    #: ``_inner`` and the budget remains. The next health check
    #: retries after the cooldown. This is the new non-terminal
    #: outcome that fixes C2.
    ATTEMPT_FAILED = "attempt_failed"
    #: The deferral ceiling was reached and forced a replacement;
    #: the replacement itself failed or succeeded. The ceiling trip
    #: itself is NOT terminal; only the resulting ``ATTEMPT_FAILED``
    #: or ``REPLACED`` outcome is recorded.
    DEFERRAL_CEILING_TRIPPED = "deferral_ceiling_tripped"
    #: A fresh candidate was spawned and verified; ``_inner`` now
    #: points at the new child. ``restart_count`` was incremented
    #: and ``_bridge_generation`` was bumped.
    REPLACED = "replaced"
    #: The old child is alive and no replacement was needed. The
    #: bridge returns ``False`` to its caller but no restart was
    #: attempted, so ``restart_count`` was NOT incremented.
    KEPT_OLD = "kept_old"


__all__ = ["RestartAttemptResult"]
