from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class OpenCodeChildPart:
    child_session_id: str
    agent: str | None
    title: str
    part_id: str
    kind: str
    time_updated_ms: int


def part_kind_from_data(data: str) -> str:
    try:
        decoded: object = json.loads(data)
    except (TypeError, ValueError):
        return "part"
    if not isinstance(decoded, dict):
        return "part"
    payload = {str(key): value for key, value in decoded.items()}
    part_type = payload.get("type")
    if not isinstance(part_type, str) or not part_type:
        return "part"
    if part_type == "tool":
        tool = payload.get("tool")
        if isinstance(tool, str) and tool:
            return f"tool:{tool}"
    return part_type


def summarize_child_part(part: OpenCodeChildPart) -> str:
    label = f"[child:{part.agent}]" if part.agent else "[child]"
    if part.kind.startswith("tool:"):
        verb = f"tool_use:{part.kind[len('tool:') :]}"
    elif part.kind == "reasoning":
        verb = "thinking:"
    elif part.kind == "text":
        verb = "text:"
    else:
        verb = f"{part.kind}:"
    return f"{verb} {label} {part.title}".rstrip()


__all__ = ["OpenCodeChildPart", "part_kind_from_data", "summarize_child_part"]
