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
        restart_reason: str | None = None,
        handover_step: str | None = None,
        probe_duration_s: float | None = None,
        probe_timeout_s: float | None = None,
        child_pid: int | None = None,
        psutil_status: str | None = None,
        startup_diagnostic: str = "",
        failure_kind: str = "transient",
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
        # richer failure diagnostics (C5)
        self.restart_reason: str | None = restart_reason
        self.handover_step: str | None = handover_step
        self.probe_duration_s: float | None = probe_duration_s
        self.probe_timeout_s: float | None = probe_timeout_s
        self.child_pid: int | None = child_pid
        self.psutil_status: str | None = psutil_status
        self.startup_diagnostic: str = startup_diagnostic
        self.failure_kind: str = failure_kind
        super().__init__(message)

    def __str__(self) -> str:
        parts = [super().__str__()]
        parts.append(f"restart_count={self.restart_count}")
        if self.last_stage is not None:
            parts.append(f"last_stage={self.last_stage}")
        if self.elapsed_s is not None:
            parts.append(f"elapsed_s={self.elapsed_s}")
        if self.restart_reason is not None:
            parts.append(f"restart_reason={self.restart_reason}")
        if self.handover_step is not None:
            parts.append(f"handover_step={self.handover_step}")
        if self.probe_duration_s is not None:
            parts.append(f"probe_duration_s={self.probe_duration_s}")
        if self.probe_timeout_s is not None:
            parts.append(f"probe_timeout_s={self.probe_timeout_s}")
        if self.child_pid is not None:
            parts.append(f"child_pid={self.child_pid}")
        if self.psutil_status is not None:
            parts.append(f"psutil_status={self.psutil_status}")
        if self.startup_diagnostic:
            parts.append(f"startup_diagnostic={self.startup_diagnostic}")
        parts.append(f"failure_kind={self.failure_kind}")
        return "; ".join(parts)


__all__ = ["McpServerError"]
