"""Shared parser-metadata correlation for logical tool calls."""

from __future__ import annotations


def tool_call_id(metadata: dict[str, object]) -> str | None:
    """Return a tool-call id without mistaking unrelated event ids for calls."""
    for key in ("tool_call_id", "toolCallId", "tool_use_id"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return value
    tool_call = metadata.get("toolCall")
    if isinstance(tool_call, dict):
        value = tool_call.get("id")
        if isinstance(value, str) and value:
            return value
    if metadata.get("type") == "toolCall":
        value = metadata.get("id")
        if isinstance(value, str) and value:
            return value
    return None
