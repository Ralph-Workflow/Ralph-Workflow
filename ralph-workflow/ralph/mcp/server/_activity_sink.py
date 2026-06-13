"""Per-task MCP activity sink registry.

The Ralph idle watchdog defers a ``NO_OUTPUT_DEADLINE`` fire when a non-stdout
channel (MCP tool call, subagent work, workspace event) is fresher than
``activity_evidence_ttl_seconds``. The MCP server's ``_handle_tools_call``
path records each successful and failing tool invocation by invoking an
"activity sink" callable — typically the per-run watchdog's
``record_mcp_tool_call`` method.

This module holds the per-task registry. Concurrent agent runs in the
same process do not stomp on each other because the registry is backed
by a ``contextvars.ContextVar`` rather than a module-level mutable
global: each agent run runs inside its own asyncio task (or thread), and
``ContextVar`` lookups naturally return the most recently set value
within the running task's context.

Public API:

- ``set_active_sink(sink)``: register an activity sink for the current
  task context. Returns a token that can be passed to ``reset_active_sink``
  for an explicit reset. ``None`` clears the sink.
- ``get_active_sink()``: return the active sink for the current task, or
  ``None`` if no sink has been registered.
- ``reset_active_sink(token)``: restore the sink to a previous token
  value (e.g. when the sink is set inside a ``with`` block and must be
  restored on exit).
- ``invoke_active_sink(tool_name)``: call the active sink with the given
  tool name, or no-op when no sink is registered. Safe to call from any
  code path; never raises.

The watchdog is the canonical caller of ``set_active_sink``: it
registers its own ``record_mcp_tool_call`` method when an agent run
begins, and unregisters it (via ``reset_active_sink`` or by setting
``None``) when the run ends.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar, Token

ActivitySink = Callable[[str], None]

_active_sink: ContextVar[ActivitySink | None] = ContextVar("ralph_mcp_activity_sink", default=None)


def set_active_sink(sink: ActivitySink | None) -> Token[ActivitySink | None]:
    """Register an activity sink for the current task context.

    Args:
        sink: Callable invoked with a single ``tool_name`` string argument
            on every tools/call invocation that the MCP server processes.
            Pass ``None`` to clear the sink.

    Returns:
        A Token that can be passed to ``reset_active_sink`` to restore the
        previous sink. The caller is responsible for unsetting the sink
        when the run ends (typically via a ``finally`` block).
    """
    return _active_sink.set(sink)


def get_active_sink() -> ActivitySink | None:
    """Return the active sink for the current task, or None if none is set."""
    return _active_sink.get()


def reset_active_sink(token: Token[ActivitySink | None]) -> None:
    """Restore the sink to a previous token value (from ``set_active_sink``)."""
    _active_sink.reset(token)


def invoke_active_sink(tool_name: str) -> None:
    """Call the active sink with the given tool name, no-op when none is set.

    The watchdog's ``record_mcp_tool_call`` is the canonical sink; this
    helper is the single emission point the MCP server uses so a future
    change to the sink protocol only touches one place. The helper is
    deliberately exception-swallowing: a buggy sink must not crash the
    JSON-RPC dispatch path.
    """
    sink = _active_sink.get()
    if sink is None:
        return
    try:
        sink(tool_name)
    except Exception:
        # A buggy sink must not crash the JSON-RPC dispatch path. The
        # watchdog already logs recorder errors at DEBUG; we keep this
        # path equally quiet so the MCP server remains fail-soft.
        return


# ---------------------------------------------------------------------------
# Subagent activity sink (parallel contextvar for OpenCodeExecutionStrategy).
# ---------------------------------------------------------------------------
# The OpenCode strategy is constructed in ``invoke_agent`` BEFORE the
# reader's per-run watchdog exists, so we cannot bind the sink at
# strategy construction time. Instead, the reader registers the
# watchdog's ``record_subagent_work`` method via ``set_subagent_sink``
# before its lines loop starts, and the strategy consults the
# contextvar from inside ``observe_line`` for each child progress /
# heartbeat signal. The contextvar isolates concurrent agent runs in
# the same process.

_subagent_sink: ContextVar[ActivitySink | None] = ContextVar(
    "ralph_subagent_activity_sink", default=None
)


def set_subagent_sink(sink: ActivitySink | None) -> Token[ActivitySink | None]:
    """Register a subagent activity sink for the current task context.

    Args:
        sink: Callable invoked with a single ``line`` string argument
            on every child progress / heartbeat signal that the
            OpenCodeExecutionStrategy observes. Pass ``None`` to clear
            the sink.

    Returns:
        A Token that can be passed to ``reset_subagent_sink`` to restore
        the previous sink. The caller is responsible for unsetting the
        sink when the run ends (typically via a ``finally`` block).
    """
    return _subagent_sink.set(sink)


def get_subagent_sink() -> ActivitySink | None:
    """Return the active subagent sink for the current task, or None if none is set."""
    return _subagent_sink.get()


def reset_subagent_sink(token: Token[ActivitySink | None]) -> None:
    """Restore the subagent sink to a previous token value (from ``set_subagent_sink``)."""
    _subagent_sink.reset(token)


def invoke_subagent_sink(line: str) -> None:
    """Call the active subagent sink with the given line, no-op when none is set.

    The watchdog's ``record_subagent_work`` is the canonical sink; this
    helper is the single emission point OpenCodeExecutionStrategy uses
    so a future change to the sink protocol only touches one place. The
    helper is deliberately exception-swallowing: a buggy sink must not
    crash the registry update path.
    """
    sink = _subagent_sink.get()
    if sink is None:
        return
    try:
        sink(line)
    except Exception:
        return
