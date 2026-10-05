"""Sanity-only detector for plan artifact text."""

from __future__ import annotations

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
    "as an ai ",
    "as an ai,",  # honor explicit AI-self-description with comma follow-on
    "as an ai.",
    "as an ai\n",
    "as an ai\t",
)
_PLACEHOLDERS = ("plan goes here", "todo: plan", "todo plan", "fixme: plan", "tbd: plan")


def _message(reason: str) -> Diagnostic:
    return Diagnostic(
        1,
        None,
        RULE_ID,
        f"submitted text is not a usable plan: {reason}; resolve by submitting readable plan prose with at least ten words",
        "error",
    )


def _is_readable(character: str) -> bool:
    """Return whether a character is ordinary readable text or whitespace."""
    return character in " \t\r\n" or (
        character.isprintable() and not unicodedata.category(character).startswith("C")
    )


def detect_not_a_plan(text: str) -> list[Diagnostic]:
    """Return PLAN001 only for unreadable, tiny, or obvious non-plan text.

    Plan structure, frontmatter, headings, and field conventions are all
    intentionally irrelevant at this boundary.
    """
    reason = _sanity_failure_reason(text)
    return [_message(reason)] if reason is not None else []


def _sanity_failure_reason(text: str) -> str | None:
    """Identify a single explicit sanity failure without interpreting shape."""
    try:
        text.encode("utf-8")
        utf8_error = False
    except UnicodeEncodeError:
        utf8_error = True
    readable = len(text) if text.isprintable() else sum(_is_readable(character) for character in text)
    words = text.split(maxsplit=_MIN_WORDS)
    first_line = next((line.strip().casefold() for line in text.splitlines() if line.strip()), "")
    if utf8_error:
        reason = "text is not valid UTF-8"
    elif "\x00" in text:
        reason = "text contains a NUL character"
    elif not text.strip():
        reason = "text is empty"
    elif readable / len(text) < _READABLE_RATIO:
        reason = "text is control-heavy or binary-like"
    elif len(words) < _MIN_WORDS:
        reason = f"text has only {len(words)} words"
    elif first_line.startswith(_REFUSAL_PREFIXES):
        reason = "text is an obvious refusal"
    elif any(first_line.startswith(placeholder) for placeholder in _PLACEHOLDERS):
        # Anchor placeholder detection to the first non-empty line so
        # a plan that *mentions* a placeholder ("remove the obsolete
        # todo: plan placeholder") is not itself flagged as one. A real
        # placeholder text starts with the marker; a discussion of the
        # marker appears later in the line and is left alone.
        reason = "text is an obvious placeholder"
    else:
        reason = None
    return reason


__all__ = ["RULE_ID", "detect_not_a_plan"]
