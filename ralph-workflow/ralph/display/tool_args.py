"""Utilities for formatting tool_use input arguments as a compact display string."""

from __future__ import annotations

from typing import Final, cast

_KNOWN_KEY_ORDER: Final[tuple[str, ...]] = ("path", "command", "workdir", "pattern")

_MCP_RALPH_PREFIX: Final[str] = "mcp__ralph__"
_FRIENDLY_PREFIX: Final[str] = "ralph."


def friendly_tool_name(name: str) -> str:
    """Return a shorter display name for well-known MCP tool prefixes.

    ``mcp__ralph__read_file`` becomes ``ralph.read_file``.
    All other names are returned unchanged.
    Only the rendered display string is affected; metadata is untouched.
    """
    if name.startswith(_MCP_RALPH_PREFIX):
        return _FRIENDLY_PREFIX + name[len(_MCP_RALPH_PREFIX):]
    return name


def format_tool_input(input_obj: object, *, max_value_chars: int = 120) -> str:
    """Format a tool_use input dict as a compact key=value string.

    Returns empty string for non-dict inputs.
    Formats as: (k=v k=v ...) with known keys first (path, command, workdir, pattern),
    then remaining keys alphabetically. Values are truncated at max_value_chars.
    """
    if not isinstance(input_obj, dict):
        return ""
    d = cast("dict[str, object]", input_obj)
    if not d:
        return ""

    def _fmt(v: object) -> str:
        s = str(v).replace("\n", " ")
        if len(s) > max_value_chars:
            return s[:max_value_chars] + "…"
        return s

    known = [k for k in _KNOWN_KEY_ORDER if k in d]
    remaining = sorted(k for k in d if k not in _KNOWN_KEY_ORDER)
    parts = [f"{k}={_fmt(d[k])}" for k in known + remaining]
    return f"({' '.join(parts)})"


__all__ = ["format_tool_input", "friendly_tool_name"]
