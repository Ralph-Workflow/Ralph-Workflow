"""Typed boundary error for failures before an agent process starts."""

from __future__ import annotations

_E2BIG_ERRNO = 7


class AgentLaunchError(OSError):
    """The runtime could not execute an agent command."""

    def __init__(self, agent_name: str, exc: OSError, payload_bytes: int) -> None:
        self.agent_name = agent_name
        self.returncode = -1
        self.payload_bytes = payload_bytes
        self.failure_origin = "runtime_launch"
        self.issuer: str | None = None
        super().__init__(
            _E2BIG_ERRNO,
            f"runtime could not launch {agent_name!r} (E2BIG: argv+env = {payload_bytes} bytes): {exc}",
        )


__all__ = ["AgentLaunchError"]
