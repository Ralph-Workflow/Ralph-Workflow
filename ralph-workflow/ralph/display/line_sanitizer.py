
from __future__ import annotations

import re

from rich.text import Text

_TERMINAL_ESCAPE_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]"
    r"|[\]PX^_][^\x1b\x07]*(?:\x07|\x1b\\)"
    r"|[\]PX^_][^\x1b\x07]*$"
    r"|\[[^\x1b]*$"
    r"|[@-Z\\-_])"
)

_C0_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def strip_terminal_control(text: str) -> str:
    if not text:
        return ""
    cleaned = _TERMINAL_ESCAPE_RE.sub("", text)
    cleaned = _C0_CONTROL_RE.sub("", cleaned)
    return cleaned


def strip_markup_safe(text: str) -> str:
    if not text:
        return ""
    if "[/" not in text:
        return strip_terminal_control(text)
    try:
        plain = Text.from_markup(text).plain
    except Exception:
        plain = text
    return strip_terminal_control(plain)


def sanitize_display_line(raw: bytes | str, max_chars: int = 200) -> str:
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = strip_terminal_control(text)

    if len(text) > max_chars:
        text = text[:max_chars] + "…"

    return text


__all__ = ["sanitize_display_line", "strip_markup_safe", "strip_terminal_control"]
