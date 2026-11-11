
from __future__ import annotations

__all__ = ["strip_duplicate_tool_prefix"]


def strip_duplicate_tool_prefix(body: str, tool_name: str) -> str:
    if not tool_name or not body:
        return body
    prefix = f"{tool_name} "
    if body.casefold().startswith(prefix.casefold()):
        return body[len(prefix) :]
    if body.casefold() == tool_name.casefold():
        return ""
    return body
