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
from ralph.process.manager import get_process_manager

if TYPE_CHECKING:
    from collections.abc import Callable

DEFAULT_INTERVAL = timedelta(seconds=2)

#: Label prefix every agent subprocess is spawned under (see
#: ``ralph.agents.invoke._process_reader`` and ``._pty_runner``, which both
#: use ``label=f"invoke:{...}"``). Terminating this prefix releases an agent
#: that is blocked on MCP calls the server can no longer answer.
AGENT_PROCESS_LABEL_PREFIX = "invoke:"

#: Grace period for the terminal-failure interrupt. The MCP server is
#: already unrecoverable at this point, so the agent has nothing left to
#: finish; the window only lets it flush output before SIGKILL.
_INTERRUPT_GRACE_PERIOD_S = 0.5


def _terminate_agent_processes(label_prefix: str) -> None:
    """Terminate every live agent subprocess under ``label_prefix``."""
    get_process_manager().shutdown_all_for_label(
        label_prefix, grace_period_s=_INTERRUPT_GRACE_PERIOD_S
    )


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
        check_interval: timedelta = DEFAULT_INTERVAL,
        on_restart: Callable[[int], None] | None = None,
        on_error: Callable[[McpServerError], None] | None = None,
        terminate_agents: Callable[[str], None] = _terminate_agent_processes,
    ) -> None:
        self._bridge = bridge
        self._check_interval = check_interval
        self._on_restart = on_restart
        self._on_error = on_error
        self._terminate_agents = terminate_agents
        self._mcp_error: McpServerError | None = None
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="mcp-supervisor")

    def _do_check_once(self) -> bool:
        """Execute one health check. Returns True if a restart occurred.

        Raises :class:`~ralph.mcp.server.lifecycle.McpServerError` if the
        restart budget is exhausted. The error is also stored in ``_mcp_error``
        and passed to the ``on_error`` callback before being raised.
        """
        try:
            restarted = self._bridge.check_health_and_restart_if_needed()
            if restarted and self._on_restart is not None:
                self._on_restart(self._bridge.restart_count)
            return restarted
        except McpServerError as exc:
            self._mcp_error = exc
            if self._on_error is not None:
                self._on_error(exc)
            logger.error(
                "MCP server restart budget exhausted during active agent run; restart_count={}: {}",
                exc.restart_count,
                exc,
            )
            raise

    def _interrupt_blocked_invocation(self, exc: McpServerError) -> None:
        """Release an agent wedged on an MCP server that cannot recover.

        Best-effort: a failure to terminate is logged rather than raised,
        because this runs on the supervisor thread where an escaping
        error would only lose the diagnosis stored on ``_mcp_error``.
        """
        logger.error(
            "MCP supervision is terminal; interrupting agent processes under {!r} so the"
            " run fails instead of hanging: {}",
            AGENT_PROCESS_LABEL_PREFIX,
            exc,
        )
        try:
            self._terminate_agents(AGENT_PROCESS_LABEL_PREFIX)
        except Exception:
            logger.exception("Failed to interrupt agent processes after terminal MCP failure")

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
                self._do_check_once()
            except McpServerError as exc:
                # Budget exhausted: terminal by design, and already stored
                # on _mcp_error for __exit__ to re-raise on the main thread.
                # __exit__ cannot run while the agent is still blocked on
                # MCP calls this server will never answer, so the stored
                # error would sit unread and the run would hang. Interrupt
                # the agent so the invocation returns and the failure
                # reaches the recovery controller.
                self._interrupt_blocked_invocation(exc)
                return
            except Exception:
                # Anything else is a transient supervision failure. Letting
                # it escape kills this thread, and a dead supervisor means
                # nothing ever probes or restarts the server again — the run
                # then hangs until the idle watchdog fires. Keep supervising.
                logger.exception("MCP supervisor health check failed; continuing to supervise")


__all__ = ["AGENT_PROCESS_LABEL_PREFIX", "DEFAULT_INTERVAL", "McpSupervisor"]
