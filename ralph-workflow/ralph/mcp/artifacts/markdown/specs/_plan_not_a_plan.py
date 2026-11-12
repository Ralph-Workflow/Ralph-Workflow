from __future__ import annotations

import re
import unicodedata

from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic

RULE_ID = "PLAN001"
_MIN_WORDS = 10
_READABLE_RATIO = 0.9
_REFUSAL_PREFIXES = (
    "i cannot",
    "i can't",
    "i can not",
    "i'm sorry",
    "im sorry",
)
_PLACEHOLDERS = ("plan goes here", "todo: plan", "todo plan", "fixme: plan", "tbd: plan")
_OBVIOUS_REFUSAL = re.compile(
    r"^(?:"
    r"i\s+(?:cannot|can't|can\s+not)\s+(?:complete|implement)\s+this\s+request\s+because\b"
    r"|i(?:'m|m)\s+sorry,\s+i\s+cannot\s+help\s+with\s+that\s+request\b"
    r")",
    re.IGNORECASE,
)
_CONSTRAINT_CONTINUATION = re.compile(r",\s*(?:so|but)\b", re.IGNORECASE)


def _message(reason: str) -> Diagnostic:
    return Diagnostic(
        1,
        None,
        RULE_ID,
        f"submitted text is not a usable plan: {reason}; resolve by submitting readable plan prose with at least ten words",
        "error",
    )


def _is_readable(character: str) -> bool:
    return character in " \t\r\n" or (
        character.isprintable() and not unicodedata.category(character).startswith("C")
    )


def detect_not_a_plan(text: str) -> list[Diagnostic]:
    reason = _sanity_failure_reason(text)
    return [_message(reason)] if reason is not None else []


def _first_non_empty_lines(text: str, limit: int = 2) -> list[str]:
    lines: list[str] = []
    start = 0
    text_len = len(text)
    while len(lines) < limit and start < text_len:
        idx = text.find("\n", start)
        if idx == -1:
            line = text[start:].strip()
            if line:
                lines.append(line)
            break
        line = text[start:idx].strip()
        if line:
            lines.append(line)
        start = idx + 1
    return lines


def _is_whole_message_refusal(text: str) -> bool:
    non_empty_lines = _first_non_empty_lines(text, limit=2)
    if not non_empty_lines:
        return False
    first_line_cf = non_empty_lines[0].casefold()
    if not any(first_line_cf.startswith(prefix) for prefix in _REFUSAL_PREFIXES):
        return False
    if not _OBVIOUS_REFUSAL.match(first_line_cf):
        return False
    if len(non_empty_lines) > 1:
        return False
    return not _CONSTRAINT_CONTINUATION.search(non_empty_lines[0])


def _is_all_readable(text: str) -> bool:
    return text.replace("\n", " ").replace("\t", " ").replace("\r", " ").isprintable()


def _sanity_failure_reason(text: str) -> str | None:
    try:
        text.encode("utf-8")
        utf8_error = False
    except UnicodeEncodeError:
        utf8_error = True
    if utf8_error:
        return "text is not valid UTF-8"
    if "\x00" in text:
        return "text contains a NUL character"
    if not text.strip():
        return "text is empty"

    if _is_all_readable(text):
        readable = len(text)
    else:
        readable = sum(_is_readable(character) for character in text)

    words = text.split(maxsplit=_MIN_WORDS)
    non_empty_lines = _first_non_empty_lines(text, limit=1)
    first_line = non_empty_lines[0].casefold() if non_empty_lines else ""
    if readable / len(text) < _READABLE_RATIO:
        reason = "text is control-heavy or binary-like"
    elif len(words) < _MIN_WORDS:
        reason = f"text has only {len(words)} words"
    elif _is_whole_message_refusal(text):
        reason = "text is an obvious refusal"
    elif any(first_line.startswith(placeholder) for placeholder in _PLACEHOLDERS):
        reason = "text is an obvious placeholder"
    else:
        reason = None
    return reason


__all__ = ["RULE_ID", "detect_not_a_plan"]
