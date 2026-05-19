from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedInvocationRuntime:
    """Resolved runtime configuration for a single agent invocation."""

    agent_env: dict[str, str] | None = None
    server_env: dict[str, str] | None = None
    mcp_endpoint: str | None = None
