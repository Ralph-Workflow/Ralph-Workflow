"""Structural detector for the restart-from-scratch narrative pattern.

Provides a pure structural detector for opening-narrative lines an agent
emits when restarting a task from the beginning.

The module is private (leading underscore). The structural detector is pure:
no clock, no I/O, no globals beyond the compiled pattern set.

The pattern set is a module-level frozenset of compiled regexes with an
import-time invariant guard (``if``/``raise``, not ``assert``, so
``python -O`` cannot strip it) that pins:

- the set is a ``frozenset`` (immutable, no late mutation)
- the set is non-empty (a silently-empty set would disable the
  structural detector)
- the set has a positive cardinality (the same condition, expressed
  via len())

These guards are the AGENTS.md "Absolutely Zero Dead code" plus the
import-time invariant pattern applied uniformly to module-level
constants.
"""

from __future__ import annotations

import re

#: Compiled regex set of opening-narrative patterns that signal a
#: restart-from-scratch attempt. Patterns are anchored at the start of
#: the line and matched case-insensitively (the matcher lowercases the
#: input before compiling). Order does not matter; the detector picks
#: the FIRST matched line in the input sequence.
#:
#: The set is intentionally narrow and pragmatic — the most common
#: opening-narrative phrases an agent produces when it is restarting
#: from scratch rather than continuing.
_RESTART_PATTERNS: frozenset[re.Pattern[str]] = frozenset(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^i will start by reading",
        r"^first,? let me read",
        r"^let me (first )?read the current",
        r"^i need to read the (current )?(state|task|plan|prompt|context)",
        r"^i (will|ll) start by (reading|re-reading)",
        r"^starting (by|from) (reading|scratch|the beginning)",
        r"^let me begin (by|from|with) reading",
        r"^re-reading the (prompt|task|plan|context)",
    )
)


#: Import-time invariant guard. ``if``/``raise`` (NOT ``assert``) so
#: ``python -O`` cannot strip it. The structural detector must remain
#: usable: a silently-empty set would disable the safety net and let
#: the restart-from-scratch wedge slip past the zero-progress guard.
#: The check is wrapped in a function so mypy cannot prove the value
#: is statically non-empty (the function call is opaque to the static
#: analyzer) while the runtime check remains the authoritative guard.
def _check_patterns_nonempty() -> None:
    if not isinstance(_RESTART_PATTERNS, frozenset) or not _RESTART_PATTERNS:
        raise RuntimeError("_RESTART_PATTERNS must be a non-empty frozenset")


_check_patterns_nonempty()


def is_restart_narrative(line: str) -> bool:
    """Return True when ``line`` opens with a known restart-narrative pattern.

    The match is case-insensitive and stripped of leading/trailing
    whitespace. The line is matched against every pattern in
    :data:`_RESTART_PATTERNS`; any match returns True.
    """
    return _matches_any_pattern(line)


def _matches_any_pattern(line: str) -> bool:
    normalized = line.strip().lower()
    if not normalized:
        return False
    return any(pattern.match(normalized) for pattern in _RESTART_PATTERNS)


__all__ = [
    "is_restart_narrative",
]
