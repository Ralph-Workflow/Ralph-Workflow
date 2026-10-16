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
    # Minimum spacing (seconds) between consecutive ``McpServerError``-
    # class restarts. The default of 2.0 s is the production floor:
    # the lifecycle constructs ``McpRestartPolicy()`` at
    # ``lifecycle.py:623`` so every bridge inherits a named positive
    # cooldown. Probe-failure restarts (healthy server judged
    # unresponsive) stay at 0.0 s because the cooldown only fires
    # when the previous restart is in the ``McpServerError`` family,
    # and the recovery loop is NOT touched (the 1-4 ms same-signature
    # cooldown in ``_direct_mcp_recovery`` keeps its own scale for
    # ``AgentInvocationError`` retries).
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
