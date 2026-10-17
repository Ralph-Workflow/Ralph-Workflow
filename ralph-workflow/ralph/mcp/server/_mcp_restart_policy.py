"""McpRestartPolicy — bounded restart policy for the MCP server bridge."""

from __future__ import annotations

from dataclasses import dataclass


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
    # sites (``lifecycle.py`` ``check_health_and_restart_if_needed`` and
    # ``reset_tool_registry``) for every restart class: ``McpServerError``
    # restarts AND probe-failure restarts AND tool-registry-reset
    # restarts. Fresh child processes spike the same host load
    # regardless of why the previous one died, so a single shared
    # backoff covers all three surfaces. The 1-4 ms same-signature
    # cooldown in ``_direct_mcp_recovery`` is a different layer
    # (``AgentInvocationError`` retries, not MCP restarts) and is not
    # touched here.
    cooldown_seconds: float = 2.0


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


__all__ = ["McpRestartPolicy"]
