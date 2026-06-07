"""AgentInactivityTimeoutError — raised when an agent stalls without producing output."""

from __future__ import annotations

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke._agent_invocation_error import AgentInvocationError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts


class AgentInactivityTimeoutError(AgentInvocationError):
    """Raised when an agent stalls without producing output."""

    def __init__(
        self,
        agent_name: str,
        timeout_seconds: float,
        parsed_output: list[str] | None = None,
        opts: InactivityTimeoutOpts | None = None,
    ) -> None:
        _opts = opts or InactivityTimeoutOpts()
        self.timeout_seconds = timeout_seconds
        self.reason = _opts.reason
        self.session_resume_safe = _opts.session_resume_safe
        self.resumable_session_id = _opts.resumable_session_id
        if _opts.reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG:
            duration = f"{timeout_seconds:.0f}s"
            base_msg = f"Agent kept child agents alive without producing output for {duration}"
            if _opts.diagnostic:
                cum = _opts.diagnostic.get("cumulative", "?")
                scoped = _opts.diagnostic.get("scoped_child_active", "?")
                oldest = _opts.diagnostic.get("oldest_child_seconds", "?")
                ws_delta = _opts.diagnostic.get("workspace_event_delta", "?")
                lo = _opts.diagnostic.get("lifecycle_only_activity", "?")
                stderr_msg = (
                    f"{base_msg} (cumulative={cum}s, scoped_child_active={scoped},"
                    f" oldest_child_seconds={oldest}s, workspace_event_delta={ws_delta},"
                    f" lifecycle_only_activity={lo})"
                )
            else:
                stderr_msg = base_msg
        elif _opts.reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED:
            duration = f"{timeout_seconds:.0f}s"
            stderr_msg = f"Agent exceeded max session wall-clock of {duration}"
        elif _opts.reason == WatchdogFireReason.PROCESS_EXIT_HANG:
            duration = f"{timeout_seconds:.0f}s"
            stderr_msg = f"Agent subprocess closed stdout but did not exit within {duration}"
        elif _opts.reason == WatchdogFireReason.STALLED_AFTER_TOOL_RESULT:
            duration = f"{timeout_seconds:.0f}s"
            tool_name = (
                _opts.diagnostic.get("last_tool_name", "tool") if _opts.diagnostic else "tool"
            )
            stderr_msg = (
                f"Agent produced no follow-up output for {duration} after receiving a tool result"
                f" (last_tool={tool_name})"
            )
        else:
            stderr_msg = f"Agent produced no output for {timeout_seconds:.0f}s"
        super().__init__(
            agent_name,
            -1,
            stderr_msg,
            list(parsed_output) if parsed_output is not None else [],
        )


__all__ = ["AgentInactivityTimeoutError"]
