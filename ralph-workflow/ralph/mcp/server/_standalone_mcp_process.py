"""StandaloneMcpProcess — a running standalone MCP HTTP server process."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.mcp.server._mcp_server_error import McpServerError

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
        """Stop and prove the server terminal before releasing its session file.

        The session file can authorize a still-running standalone server, so it
        is deliberately retained on any failed reap.  Callers must treat that
        as a fail-closed lifecycle error rather than advance a merge/rebase
        recovery action while the server could still touch the workspace.
        """
        if self.process.poll() is None:
            self.process.terminate(grace_period_s=5.0)
        try:
            returncode = self.process.wait(timeout=5.0)
            # ``wait()`` returning an exit code is the subprocess API's
            # terminal-state proof.  The second poll additionally supports
            # wrappers whose wait result is intentionally opaque.
            terminal = returncode is not None or self.process.poll() is not None
        except Exception as exc:
            raise McpServerError(
                "standalone MCP server could not be reaped before session cleanup",
                restart_count=0,
            ) from exc
        if not terminal:
            raise McpServerError(
                "standalone MCP server did not reach terminal state before session cleanup",
                restart_count=0,
            )
        self.session_file.unlink(missing_ok=True)


__all__ = ["StandaloneMcpProcess"]
