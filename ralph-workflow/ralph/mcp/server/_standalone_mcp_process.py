from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.process.manager import ManagedProcess, ProcessTerminationIssuer

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.server._process_like import ProcessLike

SHUTDOWN_TERMINATE_GRACE_S: Final[float] = 5.0

SHUTDOWN_REAP_WAIT_S: Final[float] = 5.0


def _terminate_server_process(process: ProcessLike, *, grace_period_s: float) -> None:
    if isinstance(process, ManagedProcess):
        process.terminate(
            grace_period_s=grace_period_s,
            issuer=ProcessTerminationIssuer.MCP_SHUTDOWN,
        )
        return
    if hasattr(process, "terminate"):
        process.terminate(grace_period_s=grace_period_s)


@dataclass
class StandaloneMcpProcess:
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
        process = self.process
        if process.poll() is None:
            _terminate_server_process(process, grace_period_s=SHUTDOWN_TERMINATE_GRACE_S)
        try:
            returncode = process.wait(timeout=SHUTDOWN_REAP_WAIT_S)
            terminal = returncode is not None or process.poll() is not None
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
