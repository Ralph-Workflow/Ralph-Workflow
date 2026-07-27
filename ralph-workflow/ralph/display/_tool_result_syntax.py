"""Syntax decoration for recognized tool-result payloads."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from rich.syntax import Syntax

from ralph.display.theme import SYNTAX_BACKGROUND_TRANSPARENT, syntax_theme_for_background

if TYPE_CHECKING:
    from rich.text import Text


def append_tool_result_syntax(
    text: Text,
    body: str,
    tool_name: str,
    *,
    terminal_bg_is_light: bool | None,
) -> bool:
    """Append syntax-highlighted ``body`` when a supported payload is recognized."""
    lexer = _tool_result_lexer(tool_name, body)
    if lexer is None:
        return False
    text.append_text(
        Syntax(
            body,
            lexer,
            theme=syntax_theme_for_background(terminal_bg_is_light),
            background_color=SYNTAX_BACKGROUND_TRANSPARENT,
        ).highlight(body)
    )
    return True


def _tool_result_lexer(tool_name: str, body: str) -> str | None:
    """Return a safe lexer for shell/MCP result payloads, or ``None`` for prose."""
    if not (tool_name == "bash" or tool_name.startswith("mcp__")):
        return None
    stripped = body.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped)
        except json.JSONDecodeError:
            return None
        return "json"
    patterns = (
        (("---", "+++", "@@"), "diff"),
        (("Traceback (most recent call last):",), "pytb"),
    )
    for markers, lexer in patterns:
        if any(marker in body for marker in markers):
            return lexer
    return "yaml" if ": " in body and "\n" in body else None
