"""Sanity-only detector for plan artifact text."""

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
# ponytail: dropped ``as an ai`` family because it catches legitimate
# self-description like ``As an AI engineer`` while still requiring
# boundary word logic that the product criteria says to avoid. A
# submission beginning with that phrase is treated as a usable plan;
# genuine refusals keep failing through ``I cannot`` / ``I'm sorry``.
_PLACEHOLDERS = ("plan goes here", "todo: plan", "todo plan", "fixme: plan", "tbd: plan")
# Only a whole-message inability to complete or implement the request is
# recognizably not a plan. A finite list of plan verbs would turn ordinary
# prose vocabulary into an acceptance restriction at this sanity boundary.
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


def _is_whole_message_refusal(text: str) -> bool:
    """Return True only when the entire message is a refusal.

    A multi-line message that opens with a constraint clause ("I cannot
    implement this request because X" followed by subsequent implementation
    and verification guidance) is a constrained plan, not a refusal: the
    first line is a preface and the rest prescribes work. Only single-line
    refusals, or single-line messages whose refusal prefix is not followed by
    inline continuation, count as a whole-message inability to plan.
    """
    non_empty_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not non_empty_lines:
        return False
    first_line_cf = non_empty_lines[0].casefold()
    if not any(first_line_cf.startswith(prefix) for prefix in _REFUSAL_PREFIXES):
        return False
    if not _OBVIOUS_REFUSAL.match(first_line_cf):
        return False
    if len(non_empty_lines) > 1:
        # A refusal preface is followed by additional substantive lines;
        # treat the message as a constrained plan, not a refusal.
        return False
    return not _CONSTRAINT_CONTINUATION.search(non_empty_lines[0])


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
    elif _is_whole_message_refusal(text):
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
