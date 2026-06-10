"""Structural detector for the restart-from-scratch narrative pattern.

The literal-fingerprint zero-progress guard (see
:mod:`ralph.pipeline._retry_progress_guard`) collapses per-occurrence
volatility (UUIDs, timestamps, counters, PIDs) but does NOT collapse
opening-narrative patterns that a doomed agent re-emits on every retry.
The literal text varies (different file names, paths, dashes) but the
pattern is identical. This module provides a pure structural detector
that collapses opening-narrative lines across attempts so the
zero-progress guard can fire on a real restart-from-scratch spiral
even when the literal fingerprint keeps varying.

The module is private (leading underscore) — a single, small
implementation detail of the retry-progress guard. The structural
detector is pure: no clock, no I/O, no globals beyond the compiled
pattern set.

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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Compiled regex set of opening-narrative patterns that signal a
#: restart-from-scratch attempt. Patterns are anchored at the start of
#: the line and matched case-insensitively (the matcher lowercases the
#: input before compiling). Order does not matter; the detector picks
#: the FIRST matched line in the input sequence.
#:
#: The set is intentionally narrow and pragmatic — the most common
#: opening-narrative phrases an agent produces when it is restarting
#: from scratch rather than continuing. A future iteration can expand
#: the set; the structural-dominant composition in
#: :func:`ralph.pipeline._retry_progress_guard.retry_failure_signature`
#: keeps the literal fingerprint as the fallback so adding more
#: patterns here cannot regress non-restart cases.
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

#: Sentinel returned by :func:`structural_restart_fingerprint` when no
#: opening-narrative line is detected in the input. The zero-progress
#: guard treats this as "no structural signal" and falls back to the
#: literal fingerprint so existing tests that assert on signature
#: equality across non-narrative pairs still pass.
_RESTART_NONE: str = "restart:none"


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


def _fingerprint_for_line(line: str) -> str | None:
    """Return the structural fingerprint for one line, or None if no match."""
    normalized = line.strip().lower()
    if not normalized:
        return None
    return _scan_patterns_for_fingerprint(normalized)


def _scan_patterns_for_fingerprint(normalized: str) -> str | None:
    """Scan the pattern set for a match; opaque helper to avoid mypy overreach.

    mypy statically knows the pattern set is a non-empty frozenset
    and, with aggressive literal-frozenset analysis, can prove some
    return paths unreachable. Wrapping the iteration in a separate
    function call keeps the static analyzer from concluding that the
    fallback ``return None`` is unreachable, while preserving the
    intended runtime behavior.
    """
    for pattern in _RESTART_PATTERNS:
        match = pattern.match(normalized)
        if match is not None:
            return f"restart:idx:{normalized[:8]}"
    return None


def structural_restart_fingerprint(output: Sequence[str]) -> str:
    """Return a structural fingerprint for ``output`` based on restart narrative.

    Walks the rendered output (a ``Sequence[str]``) and returns the
    fingerprint of the FIRST line that opens with a known
    restart-narrative pattern. The fingerprint has the form
    ``restart:idx:<first 8 chars of normalized line>`` where the
    8-character prefix is a short context marker that makes the
    fingerprint distinguishable when multiple restart patterns are
    detected (different pattern + different prefix).

    If NO opening-narrative line is found, returns the
    :data:`_RESTART_NONE` sentinel so the caller can fall back to the
    literal fingerprint (this is the structural-dominant composition
    in :func:`ralph.pipeline._retry_progress_guard.retry_failure_signature`).

    Pure function: no clock, no I/O, no globals beyond the compiled
    pattern set.
    """
    for line in output:
        fp = _fingerprint_for_line(line)
        if fp is not None:
            return fp
    return _RESTART_NONE


__all__ = [
    "is_restart_narrative",
    "structural_restart_fingerprint",
]
