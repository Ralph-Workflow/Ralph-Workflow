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


__all__ = ["McpRestartPolicy"]
