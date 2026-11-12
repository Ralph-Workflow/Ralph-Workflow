from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar, Token

ActivitySink = Callable[[str], None]

_active_sink: ContextVar[ActivitySink | None] = ContextVar("ralph_mcp_activity_sink", default=None)


def set_active_sink(sink: ActivitySink | None) -> Token[ActivitySink | None]:
    return _active_sink.set(sink)


def get_active_sink() -> ActivitySink | None:
    return _active_sink.get()


def reset_active_sink(token: Token[ActivitySink | None]) -> None:
    _active_sink.reset(token)


def invoke_active_sink(tool_name: str) -> None:
    sink = _active_sink.get()
    if sink is None:
        return
    try:
        sink(tool_name)
    except Exception:
        return


_subagent_sink: ContextVar[ActivitySink | None] = ContextVar(
    "ralph_subagent_activity_sink", default=None
)


def set_subagent_sink(sink: ActivitySink | None) -> Token[ActivitySink | None]:
    return _subagent_sink.set(sink)


def get_subagent_sink() -> ActivitySink | None:
    return _subagent_sink.get()


def reset_subagent_sink(token: Token[ActivitySink | None]) -> None:
    _subagent_sink.reset(token)


def invoke_subagent_sink(line: str) -> None:
    sink = _subagent_sink.get()
    if sink is None:
        return
    try:
        sink(line)
    except Exception:
        return
