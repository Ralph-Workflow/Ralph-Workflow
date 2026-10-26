"""AgentInvocationError — base exception for agent invocation failures."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.invoke._failure_origin import FailureOrigin


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
        failure_origin: FailureOrigin = "agent",
        issuer: str | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.returncode = returncode
        self.stderr = stderr
        self.parsed_output = list(parsed_output) if parsed_output is not None else []
        self.failure_origin = failure_origin
        self.issuer = issuer
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


__all__ = ["AgentInvocationError"]
