"""McpServerError — raised when the MCP server fails and restart budget is exhausted."""

from __future__ import annotations


class McpServerError(Exception):
    """Raised when the MCP server fails and the restart budget is exhausted.

    The ``last_stage`` attribute carries the most recent visible startup
    stage the child reached before the preflight failure, sourced from
    the bounded stage-tracker file the child writes as it boots. The
    supervisor falls back to ``"last_stage_unknown"`` when the file is
    absent (e.g. a foreign process that does not implement the
    stage-tracker contract, or a child killed before the first stage
    flush). The new field is purely additive: existing kwarg-only call
    sites that pass only ``restart_count=`` continue to work, and the
    supervisor-side consumer reads the value to annotate the failure
    surfaced to ``effect_executor`` so the operator can distinguish
    "child never bound" from "child bound but readiness probe failed".
    """

    def __init__(
        self,
        message: str,
        *,
        restart_count: int,
        last_stage: str | None = None,
        elapsed_s: float | None = None,
    ) -> None:
        self.restart_count = restart_count
        # ``last_stage`` is nullable so existing call sites stay valid;
        # the consumer in :mod:`ralph.mcp.server.lifecycle` falls back
        # to ``"last_stage_unknown"`` when ``None``.
        self.last_stage: str | None = last_stage
        # ``elapsed_s`` records how long the supervisor waited before
        # the failure fired, so an operator can distinguish a
        # 1-second bind-failure from a 30-second preflight timeout.
        self.elapsed_s: float | None = elapsed_s
        super().__init__(message)


__all__ = ["McpServerError"]
