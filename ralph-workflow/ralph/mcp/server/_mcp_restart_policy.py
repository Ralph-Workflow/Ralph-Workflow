"""McpRestartPolicy — bounded restart policy for the MCP server bridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class McpRestartPolicy:
    """Bounded restart policy for the MCP server bridge."""

    # wt-024 M10 (AC-08): lowered from 1000 to 20 to prevent
    # runaway process churn from a wedged MCP server. Each restart
    # spawns a new subprocess, opens a log FD, creates a session
    # file, and runs preflight (TCP+HTTP+tools/list); 20 restarts is
    # generous for transient crashes while bounding the
    # per-invocation memory and CPU cost. The field is still
    # overridable by callers via ``McpRestartPolicy(max_restarts=N)``.
    max_restarts: int = 20
    # Minimum spacing (seconds) between consecutive MCP restarts. The
    # default of 2.0 s is the production floor: the lifecycle constructs
    # ``McpRestartPolicy()`` at ``lifecycle.py`` ``start_mcp_server`` (the
    # ``effective_extras.restart_policy or McpRestartPolicy()`` fallback)
    # so every bridge inherits a named positive cooldown. The cooldown
    # is applied UNCONDITIONALLY at the ``_cooldown_after_failure`` call
    # sites (``llyphase_agent_handler`` ``check_health_and_restart_if_needed`` and
    # ``reset_tool_registry``) for every restart class: ``McpServerError``
    # restarts AND probe-failure restarts AND tool-registry-reset
    # restarts. Fresh child processes spike the same host load
    # regardless of why the previous one died, so a single shared
    # backoff covers all three surfaces. The 1-4 ms same-signature
    # cooldown in ``_direct_mcp_recovery`` is a different layer
    # (``AgentInvocationError`` retries, not MCP restarts) and is not
    # touched here.
    cooldown_seconds: float = 2.0
    # Sliding window (seconds) within which workspace-monitor
    # ``watch_capacity_predicted`` signals are treated as fresh for
    # the bridge's host-contention guard.  Default 10.0 s — long
    # enough to cover a single probe-miss burst under load without
    # leaking stale state across recovery cycles.  Bounded strictly
    # above 6.0 s below so a future regression that drops it to a
    # value that risks eating probe-budget timeout windows trips
    # here.
    host_contention_window_seconds: float = 10.0
    # Maximum consecutive probe-failures deferred under a host-contention signal.
    # While the child process is alive and a fresh contention signal is present,
    # probe failures never escalate to restart; instead next probe timeouts
    # escalate and durable contention deferral records are written. Retained
    # for configuration and backward-compatibility. Capped in [1, 10] below.
    max_contention_deferrals: int = 3
    # Rolling-window cap: maximum restart events counted within the
    # rolling window before a "window_drain" decision is returned.
    # Default 6 — with a 60-second window this allows bursty restarts
    # while preventing a sustained restart storm from spinning forever.
    rolling_max_in_window: int = 6


#: Lower bound for the host-contention window. Must be strictly above
#: the production probe timeout (6.0 s) so a contended probe-miss
#: burst fits inside the window.
_HOST_CONTENTION_WINDOW_MIN_S: float = 6.0
#: Inclusive lower bound for the contention-deferral budget.
_MAX_CONTENTION_DEFERRALS_MIN: int = 1
#: Inclusive upper bound for the contention-deferral budget.
_MAX_CONTENTION_DEFERRALS_MAX: int = 10
#: Minimum positive value for rolling_max_in_window.
_ROLLING_MAX_IN_WINDOW_MIN: int = 1

# Import-time invariants pin the named positive production floor for
# ``cooldown_seconds``. ``if``/``raise RuntimeError`` (NOT ``assert``)
# so the checks survive ``python -O``. ``max_restarts`` is also bounded
# below so a future regression that lowers either floor trips here.
_DEFAULT_COOLDOWN_SECONDS: float = McpRestartPolicy.cooldown_seconds
_DEFAULT_MAX_RESTARTS: int = McpRestartPolicy.max_restarts
if not _DEFAULT_COOLDOWN_SECONDS > 0:
    raise RuntimeError(
        f"McpRestartPolicy.cooldown_seconds default must be positive "
        f"(got {_DEFAULT_COOLDOWN_SECONDS})"
    )
if not _DEFAULT_MAX_RESTARTS > 0:
    raise RuntimeError(
        f"McpRestartPolicy.max_restarts default must be positive (got {_DEFAULT_MAX_RESTARTS})"
    )
_DEFAULT_HOST_CONTENTION_WINDOW_S: float = McpRestartPolicy.host_contention_window_seconds
_DEFAULT_MAX_CONTENTION_DEFERRALS: int = McpRestartPolicy.max_contention_deferrals
if not _DEFAULT_HOST_CONTENTION_WINDOW_S > _HOST_CONTENTION_WINDOW_MIN_S:
    raise RuntimeError(
        f"McpRestartPolicy.host_contention_window_seconds default must be > "
        f"{_HOST_CONTENTION_WINDOW_MIN_S}s (got {_DEFAULT_HOST_CONTENTION_WINDOW_S})"
    )
if not (
    _MAX_CONTENTION_DEFERRALS_MIN
    <= _DEFAULT_MAX_CONTENTION_DEFERRALS
    <= _MAX_CONTENTION_DEFERRALS_MAX
):
    raise RuntimeError(
        f"McpRestartPolicy.max_contention_deferrals default must be in "
        f"[{_MAX_CONTENTION_DEFERRALS_MIN}, {_MAX_CONTENTION_DEFERRALS_MAX}] "
        f"(got {_DEFAULT_MAX_CONTENTION_DEFERRALS})"
    )
_DEFAULT_ROLLING_MAX_IN_WINDOW: int = McpRestartPolicy.rolling_max_in_window
if not _DEFAULT_ROLLING_MAX_IN_WINDOW > 0:
    raise RuntimeError(
        f"McpRestartPolicy.rolling_max_in_window default must be positive "
        f"(got {_DEFAULT_ROLLING_MAX_IN_WINDOW})"
    )


def next_budget_decision(
    policy: McpRestartPolicy,
    current_count: int,
    rolling_history: tuple[float, ...],
    rolling_window_s: float,
    *,
    _now: Callable[[], float] | None = None,
) -> str:
    """Return ``"ok"``, ``"window_drain"``, or ``"absolute_cap"`` based on restart budget.

    Parameters
    ----------
    policy : McpRestartPolicy
        The active restart policy containing ``max_restarts`` and
        ``rolling_max_in_window``.
    current_count : int
        Total restart attempts made so far in this session.
    rolling_history : tuple[float, ...]
        Timestamps (as from ``time.monotonic``) of past restart events.
    rolling_window_s : float
        The rolling window in seconds to evaluate.
    _now : Callable[[], float] | None
        Private test seam replacing ``time.monotonic``.

    Returns
    -------
    str
        ``"absolute_cap"`` when ``current_count >= policy.max_restarts``;
        ``"window_drain"`` when the rolling count within ``rolling_window_s``
        reaches ``policy.rolling_max_in_window``; ``"ok"`` when both checks pass.
    """
    now_fn: Callable[[], float] = _now if _now is not None else lambda: 0.0
    now = now_fn()
    if current_count >= policy.max_restarts:
        return "absolute_cap"
    rolling_count = sum(1 for ts in rolling_history if now - ts <= rolling_window_s)
    if rolling_count >= policy.rolling_max_in_window:
        return "window_drain"
    return "ok"


__all__ = ["McpRestartPolicy", "next_budget_decision"]
