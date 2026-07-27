"""Single canonical parser-channel prefix stripper.

wt-028-display S-5: the live log and the rendered record
must agree on which channel prefixes get stripped. Before S-5
two copies of the same stripper lived in
``agent_event_renderer._strip_internal_channel_prefix`` and
``presented_entry._strip_parser_channel_prefixes``, kept in
sync by comment only -- a drift hazard. This leaf module owns
the canonical stripper and is the only place the prefix list
lives.

Both call sites import :func:`strip_parser_channel_prefix` from
here so the rendered record and the live log can never drift.
"""

from __future__ import annotations

import re

#: Parser-channel prefixes that must NEVER reach an operator-facing
#: surface. The four documented kinds in their short form, with the
#: trailing colon and a single separating space. Agents that emit
#: structured output sometimes leak these prefixes into the body
#: (e.g. ``"text: internal prefix payload"``); the stripper removes
#: them at the canonical event-content normalization seam so the
#: severity word, tool name, and outcome carry the information
#: instead (DA-001 / AC-02).
_PARSER_CHANNEL_PREFIXES: tuple[str, ...] = (
    "text: ",
    "thinking: ",
    "tool_use: ",
    "tool_result: ",
)

#: Space-less sibling of :data:`_PARSER_CHANNEL_PREFIXES` -- covers the
#: ``"text:hello"`` accumulator key shape used by pi/claude when the
#: first content fragment lacks a separating space (AC-07 / S-6).
#: The space-less stripper only fires when the remainder is non-empty
#: AND does not begin with whitespace, so legitimate prose like
#: ``"text: A long form analysis"`` is preserved (the body is
#: preceded by whitespace, so the space-less rule bails out).
_PARSER_CHANNEL_PREFIXES_SPACELESS: tuple[str, ...] = (
    "text:",
    "thinking:",
    "tool_use:",
    "tool_result:",
)


_LIVE_BADGE_PREFIX = re.compile(
    r"^(?:[\u25d0\u25d1\u25d2\u25d3]\s+RUN\s+(?:\d{2}:\d{2}:\d{2}\s+)?\S+\s+)+"
)


def strip_parser_channel_prefix(content: str) -> str:
    """Return ``content`` with a leading parser-channel prefix removed.

    Order of attempts:

    1. The four canonical short-form prefixes with a trailing space
       (``text: ``, ``thinking: ``, ``tool_use: ``, ``tool_result: ``).
    2. The four space-less forms (``text:``, ``thinking:``,
       ``tool_use:``, ``tool_result:``) when the remainder is
       non-empty AND does not begin with whitespace. The whitespace
       guard keeps the rule from mangling legitimate prose that
       happens to begin with the word ``text:``.

    A body whose first token is a different word, or whose
    remainder is empty / whitespace prefixed, is returned
    unchanged.
    """
    content = _LIVE_BADGE_PREFIX.sub("", content)
    for prefix in _PARSER_CHANNEL_PREFIXES:
        if content.startswith(prefix):
            return content[len(prefix):]
    for prefix in _PARSER_CHANNEL_PREFIXES_SPACELESS:
        if content.startswith(prefix):
            remainder = content[len(prefix):]
            if remainder and not remainder[0].isspace():
                return remainder
    return content
