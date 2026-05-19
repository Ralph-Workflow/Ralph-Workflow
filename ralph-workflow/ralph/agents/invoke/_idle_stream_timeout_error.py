"""_IdleStreamTimeoutError — raised when an agent process stops producing output."""

from __future__ import annotations

from ralph.agents.idle_watchdog import WatchdogFireReason


class _IdleStreamTimeoutError(RuntimeError):
    """Raised when an agent process stops producing output for too long."""

    def __init__(
        self,
        timeout_seconds: float,
        reason: WatchdogFireReason,
        *,
        diagnostic: dict[str, str | int | float | bool] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.reason = reason
        self.diagnostic = diagnostic
        if reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG:
            duration = f"{timeout_seconds:.0f}s"
            base_msg = f"Agent kept child agents alive without producing output for {duration}"
            if diagnostic:
                cum = diagnostic.get("cumulative", "?")
                scoped = diagnostic.get("scoped_child_active", "?")
                oldest = diagnostic.get("oldest_child_seconds", "?")
                ws_delta = diagnostic.get("workspace_event_delta", "?")
                lo = diagnostic.get("lifecycle_only_activity", "?")
                msg = (
                    f"{base_msg} (cumulative={cum}s, scoped_child_active={scoped},"
                    f" oldest_child_seconds={oldest}s, workspace_event_delta={ws_delta},"
                    f" lifecycle_only_activity={lo})"
                )
            else:
                msg = base_msg
        elif reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED:
            duration = f"{timeout_seconds:.0f}s"
            msg = f"Agent exceeded max session wall-clock of {duration}"
        elif reason == WatchdogFireReason.PROCESS_EXIT_HANG:
            duration = f"{timeout_seconds:.0f}s"
            msg = f"Agent subprocess closed stdout but did not exit within {duration}"
        else:
            msg = f"Agent produced no output for {timeout_seconds:.0f}s"
        super().__init__(msg)


__all__ = ["_IdleStreamTimeoutError"]
