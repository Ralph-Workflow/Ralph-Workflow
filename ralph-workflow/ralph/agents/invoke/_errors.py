"""Agent invocation error classes."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.agents.idle_watchdog import WatchdogFireReason


class UnsupportedMcpTransportError(RuntimeError):
    """Raised when MCP-backed execution is requested for an unsupported transport."""

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

    class AgentInvocationError(Exception):
        """Raised when agent invocation fails.

        Attributes:
            agent_name: Name of the agent that failed.
            returncode: Process exit code.
            stderr: Standard error output.
        """

        def __init__(
            self,
            agent_name: str,
            returncode: int,
            stderr: str = "",
            parsed_output: list[str] | None = None,
        ) -> None:
            """Initialize invocation error.

            Args:
                agent_name: Name of the agent.
                returncode: Process exit code.
                stderr: Standard error output.
            """
            self.agent_name = agent_name
            self.returncode = returncode
            self.stderr = stderr
            self.parsed_output = list(parsed_output) if parsed_output is not None else []
            detail = self._detail_message()
            suffix = f": {detail}" if detail else ""
            super().__init__(f"Agent '{agent_name}' failed with code {returncode}{suffix}")

        def _detail_message(self) -> str:
            stderr = self.stderr.strip()
            if stderr:
                return stderr
            if self.parsed_output:
                return " | ".join(self.parsed_output)
            return ""

    class InteractivePermissionPromptError(AgentInvocationError):
        """Raised when interactive Claude reaches a permission prompt in unattended mode."""

        def __init__(self, agent_name: str, parsed_output: list[str]) -> None:
            super().__init__(
                agent_name,
                -1,
                "Interactive Claude reached a permission prompt in unattended mode",
                parsed_output,
            )

    @dataclass(frozen=True)
    class InactivityTimeoutOpts:
        """Optional parameters for AgentInactivityTimeoutError."""

        reason: WatchdogFireReason | None = None
        session_resume_safe: bool = False
        diagnostic: dict[str, str | int | float | bool] | None = None

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
            else:
                stderr_msg = f"Agent produced no output for {timeout_seconds:.0f}s"
            super().__init__(
                agent_name,
                -1,
                stderr_msg,
                list(parsed_output) if parsed_output is not None else [],
            )

    class OpenCodeResumableExitError(AgentInvocationError):
        """Raised when an agent session exits with code 0 without required
        completion evidence.

        The session can be continued; the runner maps this into a session-preserving retry.
        """

        def __init__(self, agent_name: str, session_id: str | None = None) -> None:
            self.resumable_session_id = session_id
            super().__init__(
                agent_name,
                0,
                (
                    "agent session exited without required completion evidence "
                    "(no artifact, no declare_complete)"
                ),
            )


_IdleStreamTimeoutError = UnsupportedMcpTransportError._IdleStreamTimeoutError
AgentInvocationError = UnsupportedMcpTransportError.AgentInvocationError
InteractivePermissionPromptError = UnsupportedMcpTransportError.InteractivePermissionPromptError
InactivityTimeoutOpts = UnsupportedMcpTransportError.InactivityTimeoutOpts
AgentInactivityTimeoutError = UnsupportedMcpTransportError.AgentInactivityTimeoutError
OpenCodeResumableExitError = UnsupportedMcpTransportError.OpenCodeResumableExitError
