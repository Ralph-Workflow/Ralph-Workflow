from __future__ import annotations


class McpServerError(Exception):
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
        self.last_stage: str | None = last_stage
        self.elapsed_s: float | None = elapsed_s
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
