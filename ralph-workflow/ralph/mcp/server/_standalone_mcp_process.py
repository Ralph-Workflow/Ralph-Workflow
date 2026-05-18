"""StandaloneMcpProcess — a running standalone MCP HTTP server process."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.server._process_like import ProcessLike


@dataclass
class StandaloneMcpProcess:
    """A running standalone MCP HTTP server process with its endpoint and session file."""

    endpoint: str
    process: ProcessLike
    session_file: Path

    def start(self) -> None:
        return

    def agent_endpoint_uri(self) -> str:
        return self.endpoint

    def endpoint_uri(self) -> str:
        return self.endpoint

    def shutdown(self) -> None:
        if self.process.poll() is None:
            self.process.terminate(grace_period_s=5.0)
        self.session_file.unlink(missing_ok=True)


__all__ = ["StandaloneMcpProcess"]
