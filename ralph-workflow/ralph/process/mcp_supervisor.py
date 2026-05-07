"""Active MCP server supervision during agent execution.

The McpSupervisor runs a background thread that polls bridge health on a
fixed interval while an agent attempt is executing. When the MCP server
crashes, the supervisor restarts it on the stable endpoint so the agent
can continue. If the restart budget is exhausted, the error is stored and
re-raised when the context manager exits.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.server.lifecycle import McpServerError, RestartAwareMcpBridge

if TYPE_CHECKING:
    from collections.abc import Callable

_DEFAULT_INTERVAL = timedelta(seconds=2)


class McpSupervisor:
    """Background-thread supervisor for an active MCP server bridge.

    Usage::

        with McpSupervisor(bridge, on_restart=subscriber.record_mcp_restart):
            output = invoke_agent(...)
            stream_output(output)

    The supervisor polls ``check_mcp_bridge_health(bridge)`` every
    ``check_interval`` seconds. Restarts are recorded via the optional
    ``on_restart`` callback. If the restart budget is exhausted, the stored
    :class:`~ralph.mcp.server.lifecycle.McpServerError` is re-raised when
    the context manager exits — taking priority over any agent-level error.
    """

    def __init__(
        self,
        bridge: RestartAwareMcpBridge,
        *,
        check_interval: timedelta = _DEFAULT_INTERVAL,
        on_restart: Callable[[int], None] | None = None,
    ) -> None:
        self._bridge = bridge
        self._check_interval = check_interval
        self._on_restart = on_restart
        self._mcp_error: McpServerError | None = None
        self._done = threading.Event()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="mcp-supervisor"
        )

    def __enter__(self) -> McpSupervisor:
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self._done.set()
        self._thread.join(timeout=self._check_interval.total_seconds() * 2 + 1.0)
        if self._mcp_error is not None:
            raise self._mcp_error

    def _run(self) -> None:
        interval_s = self._check_interval.total_seconds()
        while not self._done.wait(timeout=interval_s):
            try:
                restarted = self._bridge.check_health_and_restart_if_needed()
                if restarted and self._on_restart is not None:
                    self._on_restart(self._bridge.restart_count)
            except McpServerError as exc:
                self._mcp_error = exc
                logger.error(
                    "MCP server restart budget exhausted during active agent run; "
                    "restart_count={}: {}",
                    exc.restart_count,
                    exc,
                )
                return


__all__ = ["McpSupervisor"]
