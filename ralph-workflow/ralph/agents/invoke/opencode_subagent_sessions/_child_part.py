"""One updated message part of a native OpenCode subagent, and its summaries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True)
class OpenCodeChildPart:
    """One updated message part belonging to a native OpenCode subagent."""

    child_session_id: str
    agent: str | None
    title: str
    part_id: str
    kind: str
    time_updated_ms: int


def part_kind_from_data(data: str) -> str:
    """Summarise a ``part.data`` JSON payload as ``tool:<name>`` / ``text`` / ``reasoning``."""
    try:
        decoded = cast("object", json.loads(data))
    except (TypeError, ValueError):
        return "part"
    if not isinstance(decoded, dict):
        return "part"
    payload = cast("dict[str, object]", decoded)
    part_type = payload.get("type")
    if not isinstance(part_type, str) or not part_type:
        return "part"
    if part_type == "tool":
        tool = payload.get("tool")
        if isinstance(tool, str) and tool:
            return f"tool:{tool}"
    return part_type


def summarize_child_part(part: OpenCodeChildPart) -> str:
    """Render a part as the ``verb:<name> [child:<agent>] <title>`` watchdog summary.

    The ``tool_use:`` / ``text:`` / ``thinking:`` prefixes are the canonical
    subagent-description vocabulary the watchdog surfaces as the current
    tool call; the ``[child:<agent>]`` label keeps the line attributed to
    the subagent rather than the parent.
    """
    label = f"[child:{part.agent}]" if part.agent else "[child]"
    if part.kind.startswith("tool:"):
        verb = f"tool_use:{part.kind[len('tool:'):]}"
    elif part.kind == "reasoning":
        verb = "thinking:"
    elif part.kind == "text":
        verb = "text:"
    else:
        verb = f"{part.kind}:"
    return f"{verb} {label} {part.title}".rstrip()


__all__ = ["OpenCodeChildPart", "part_kind_from_data", "summarize_child_part"]
