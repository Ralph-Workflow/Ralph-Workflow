from __future__ import annotations


class McpServerError(Exception):
    restart_count: int
    last_stage: str | None
    elapsed_s: float | None
    restart_reason: str | None
    handover_step: str | None
    probe_duration_s: float | None
    probe_timeout_s: float | None
    child_pid: int | None
    psutil_status: str | None
    startup_diagnostic: str
    failure_kind: str

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
        object.__setattr__(self, "restart_count", restart_count)
        object.__setattr__(self, "last_stage", last_stage)
        object.__setattr__(self, "elapsed_s", elapsed_s)
        object.__setattr__(self, "restart_reason", restart_reason)
        object.__setattr__(self, "handover_step", handover_step)
        object.__setattr__(self, "probe_duration_s", probe_duration_s)
        object.__setattr__(self, "probe_timeout_s", probe_timeout_s)
        object.__setattr__(self, "child_pid", child_pid)
        object.__setattr__(self, "psutil_status", psutil_status)
        object.__setattr__(self, "startup_diagnostic", startup_diagnostic)
        object.__setattr__(self, "failure_kind", failure_kind)
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


class McpOutcomeUnknownError(McpServerError, OSError):
    tool_name: str | None

    def __init__(
        self,
        message: str = "In-flight non-idempotent MCP tool call severed; outcome unknown",
        *,
        restart_count: int = 0,
        tool_name: str | None = None,
        last_stage: str | None = None,
        elapsed_s: float | None = None,
        restart_reason: str | None = None,
        handover_step: str | None = None,
        probe_duration_s: float | None = None,
        probe_timeout_s: float | None = None,
        child_pid: int | None = None,
        psutil_status: str | None = None,
        startup_diagnostic: str = "",
    ) -> None:
        object.__setattr__(self, "tool_name", tool_name)
        super().__init__(
            message,
            restart_count=restart_count,
            last_stage=last_stage,
            elapsed_s=elapsed_s,
            restart_reason=restart_reason,
            handover_step=handover_step,
            probe_duration_s=probe_duration_s,
            probe_timeout_s=probe_timeout_s,
            child_pid=child_pid,
            psutil_status=psutil_status,
            startup_diagnostic=startup_diagnostic,
            failure_kind="outcome_unknown",
        )


__all__ = ["McpOutcomeUnknownError", "McpServerError"]
