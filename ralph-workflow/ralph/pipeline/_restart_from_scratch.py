from __future__ import annotations

import re

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


def _check_patterns_nonempty() -> None:
    if not isinstance(_RESTART_PATTERNS, frozenset) or not _RESTART_PATTERNS:
        raise RuntimeError("_RESTART_PATTERNS must be a non-empty frozenset")


_check_patterns_nonempty()


def is_restart_narrative(line: str) -> bool:
    return _matches_any_pattern(line)


def _matches_any_pattern(line: str) -> bool:
    normalized = line.strip().lower()
    if not normalized:
        return False
    return any(pattern.match(normalized) for pattern in _RESTART_PATTERNS)


__all__ = [
    "is_restart_narrative",
]
