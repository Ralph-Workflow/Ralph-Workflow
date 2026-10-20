"""StandaloneMcpProcess — a running standalone MCP HTTP server process."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ralph.mcp.server._mcp_server_error import McpServerError

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.server._process_like import ProcessLike

#: Upper bound on the graceful SIGTERM window granted to a child before the
#: process manager escalates to SIGKILL. Named so the restart-budget
#: arithmetic (E6.4) can be derived from the real teardown bound instead of a
#: re-typed literal that can silently drift from the call below.
SHUTDOWN_TERMINATE_GRACE_S: Final[float] = 5.0

#: Upper bound on the post-signal reap wait proving the child reached a
#: terminal state before its session file is released.
SHUTDOWN_REAP_WAIT_S: Final[float] = 5.0


@dataclass
class StandaloneMcpProcess:
    """A running standalone MCP HTTP server process with its endpoint and session file."""

    endpoint: str
    process: ProcessLike
    session_file: Path
    stage_id: str | None = None
    handover_parent_sock: object | None = None

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
            self.process.terminate(grace_period_s=SHUTDOWN_TERMINATE_GRACE_S)
        try:
            returncode = self.process.wait(timeout=SHUTDOWN_REAP_WAIT_S)
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
        if self.handover_parent_sock is not None and hasattr(self.handover_parent_sock, "close"):
            with contextlib.suppress(Exception):
                self.handover_parent_sock.close()
            self.handover_parent_sock = None
        if hasattr(self.process, "cleanup_orphans"):
            with contextlib.suppress(Exception):
                self.process.cleanup_orphans()
        self.session_file.unlink(missing_ok=True)


__all__ = ["SHUTDOWN_REAP_WAIT_S", "SHUTDOWN_TERMINATE_GRACE_S", "StandaloneMcpProcess"]
