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
# Plan action verbs used to discriminate a real refusal from an
# actionable plan that merely *starts* with a constraint clause
# (e.g. ``I cannot change the public API without breaking
# compatibility, so implement the fix internally...``). The
# regex matches whole words in a case-insensitive pass over the
# whole text; the count of distinct action verbs anywhere in the
# text is what determines whether the prefix is read as a refusal
# or as a constraint clause leading into implementation work.
_PLAN_ACTION_VERBS: tuple[str, ...] = (
    "implement",
    "fix",
    "verify",
    "test",
    "add",
    "update",
    "modify",
    "change",
    "refactor",
    "create",
    "remove",
    "delete",
    "document",
    "write",
    "replace",
    "restructure",
    "optimize",
    "migrate",
    "build",
    "design",
    "run",
    "execute",
    "extend",
    "integrate",
    "configure",
    "install",
    "restore",
    "resolve",
    "address",
    "expose",
    "scaffold",
    "draft",
    "define",
    "dispatch",
    "route",
    "ship",
    "deploy",
    "land",
    "commit",
    "push",
    "patch",
    "repair",
    "enable",
    "disable",
    "prove",
    "demonstrate",
    "validate",
    "check",
    "confirm",
    "trace",
    "isolate",
    "reproduce",
)
_ACTION_VERB_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:" + "|".join(_PLAN_ACTION_VERBS) + r")\b",
    re.IGNORECASE,
)
# Minimum number of distinct plan action verbs required for a
# refusal-prefixed text to be read as an actionable plan rather
# than a refusal. One occurrence is the natural baseline: a real
# plan mentions a concrete action; a real refusal mentions none.
_MIN_ACTION_VERBS_FOR_ACTIONABLE_PLAN = 1


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


def _has_actionable_intent(text: str) -> bool:
    """Return whether ``text`` contains at least one plan action verb.

    Used to discriminate an actionable plan that starts with a
    constraint clause (``I cannot change the API, so implement...``)
    from a real refusal (``I cannot complete this request because
    I cannot access the repository secrets``). The match is
    case-insensitive and bounded by word boundaries so common
    English words like ``help`` and ``access`` do not false-positive
    the count.
    """
    matches: list[str] = _ACTION_VERB_PATTERN.findall(text)
    return len(matches) >= _MIN_ACTION_VERBS_FOR_ACTIONABLE_PLAN


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
    elif first_line.startswith(_REFUSAL_PREFIXES) and not _has_actionable_intent(text):
        # A refusal is a refusal only when no implementation work is
        # described anywhere in the text. A plan that opens with a
        # constraint clause ("I cannot change the public API without
        # breaking compatibility, so implement the fix internally
        # and verify existing callers with regression tests.") and
        # then prescribes implementation work is an actionable
        # plan, not a refusal; the constraint is the lead-in to the
        # prescribed work, not the whole message.
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
