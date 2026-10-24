# property-test: L — structural restart-from-scratch fingerprint
"""Structural detector for the restart-from-scratch pattern.

The literal-fingerprint zero-progress guard collapses per-occurrence
volatility (UUIDs, timestamps, counters, PIDs) but does NOT collapse the
opening-narrative pattern that a doomed agent re-emits on every retry.
The literal text varies (different file names, paths, dashes) but the
pattern is identical. This module provides a pure structural detector
that collapses opening-narrative lines across attempts so the zero-progress
guard can fire on a real restart-from-scratch spiral.

The detector is pure: no clock, no I/O, no globals beyond the compiled
pattern set. Black-box tests, no real subprocess, no wall clock.

The contract is pinned by two table-driven passes: one over the narrative
lines (does the detector fire, and does it fire for the RIGHT reason?) and
one over the rendered output sequences (what EXACTLY does the structural
fingerprint collapse to?). A length-only check ("all three fingerprints are
equal") would pass for the wrong fingerprint value, so the tables assert the
exact fingerprint strings.
"""

from __future__ import annotations

import re

import ralph.pipeline._restart_from_scratch as _restart_module
from ralph.pipeline._restart_from_scratch import (
    _RESTART_PATTERNS,
    is_restart_narrative,
    structural_restart_fingerprint,
)

#: The canonical restart line from the PROMPT.md bug log.
_CANONICAL_RESTART_LINE = (
    "I will start by reading the current state of the task, the "
    "analysis feedback, and the existing plan draft in parallel."
)

#: The structural fingerprint the detector collapses an opening narrative
#: to: ``restart:idx:`` plus the first eight characters of the normalized
#: (stripped + lowercased) line.
_EXPECTED_NARRATIVE_FINGERPRINT = "restart:idx:i will s"

#: The sentinel returned when NO opening-narrative line is detected.
_EXPECTED_NO_FINGERPRINT = "restart:none"

#: ``(line, is_restart_narrative)`` rows. The positive rows cover EVERY
#: pattern in ``_RESTART_PATTERNS`` (asserted by
#: :func:`test_known_pattern_set_is_fully_exercised`); the negative row pins
#: that genuine progress is NOT mistaken for a restart.
_NARRATIVE_CASES: tuple[tuple[str, bool], ...] = (
    (_CANONICAL_RESTART_LINE, True),
    # Genuine progress must NOT be detected as a restart narrative.
    ("I have completed step 1 and am moving to step 2.", False),
    # The detector matches uppercase, lowercase, and title-case renderings.
    ("I WILL START BY READING THE PROMPT", True),
    ("i will start by reading the prompt", True),
    ("I Will Start By Reading The Prompt", True),
    # Different concrete tokens in the same opening-narrative line all
    # flag. The literal fingerprint (RepetitionTracker.fingerprint +
    # _NUMERIC_TOKEN) does NOT collapse path tokens like /tmp/PROMPT.md vs
    # /tmp/PROMPT_v2.md -- the chars beyond the numeric vocabulary
    # diverge. The structural detector is the safety net that catches this.
    ("I will start by reading the prompt at /tmp/PROMPT.md", True),
    ("I will start by reading the prompt at /tmp/PROMPT_v2.md", True),
    ("I will start by reading the prompt at /tmp/PROMPT_draft.md", True),
    ("I will start by reading the prompt at /Users/me/PROMPT-2026.md", True),
    # One realistic line per known pattern in the known set.
    ("I will start by reading the prompt.", True),
    ("First, let me read the current state.", True),
    ("First let me read the prompt.", True),
    ("Let me first read the current task.", True),
    ("Let me read the current prompt.", True),
    ("I need to read the current state of the task.", True),
    ("I need to read the task description.", True),
    ("I will start by re-reading the prompt.", True),
    ("I will start by reading the analysis feedback.", True),
    ("Starting by reading the prompt file.", True),
    ("Starting from scratch with the prompt.", True),
    ("Starting from the beginning again.", True),
    ("Let me begin by reading the plan.", True),
    ("Let me begin from reading the spec.", True),
    ("Let me begin with reading the requirements.", True),
    ("Re-reading the prompt.", True),
    ("Re-reading the task description.", True),
)

#: ``(output_sequence, expected_fingerprint)`` rows. The fingerprint is
#: built from the FIRST matched line, so a later line never changes it --
#: that is what makes the function deterministic and lets a long transcript
#: with a single restart opener still be recognized.
_FINGERPRINT_CASES: tuple[tuple[tuple[str, ...], str], ...] = (
    # Structurally-similar opening narratives collapse to ONE fingerprint
    # even though the literal signature drifts (different concrete file
    # names). This is the property the zero-progress guard consumes.
    (("I will start by reading the prompt at /tmp/PROMPT.md",), _EXPECTED_NARRATIVE_FINGERPRINT),
    (("I will start by reading the prompt at /tmp/PROMPT_v2.md",), _EXPECTED_NARRATIVE_FINGERPRINT),
    (
        ("I will start by reading the prompt at /tmp/PROMPT_draft.md",),
        _EXPECTED_NARRATIVE_FINGERPRINT,
    ),
    # No opening-narrative line found -> the ``restart:none`` sentinel, so
    # the caller falls back to the literal fingerprint.
    (("All good, made progress on the task today",), _EXPECTED_NO_FINGERPRINT),
    (("Completed step 3 of the plan, will continue with step 4",), _EXPECTED_NO_FINGERPRINT),
    # The fingerprint comes from the FIRST matched line, not the last.
    (
        (
            "All good progress notes",
            "I will start by reading the prompt",
            "Now reading file /etc/PROMPT.md",
        ),
        _EXPECTED_NARRATIVE_FINGERPRINT,
    ),
    (
        (
            "All good progress notes",
            "I will start by reading the prompt",
            "Now reading file /etc/PROMPT_v9.md",
        ),
        _EXPECTED_NARRATIVE_FINGERPRINT,
    ),
    # Lines after the first match do not change the fingerprint.
    (("I will start by reading the prompt", "subsequent line A"), _EXPECTED_NARRATIVE_FINGERPRINT),
    (
        ("I will start by reading the prompt", "completely different second line B"),
        _EXPECTED_NARRATIVE_FINGERPRINT,
    ),
    # Degenerate inputs return the sentinel instead of raising.
    ((), _EXPECTED_NO_FINGERPRINT),
    (("", "  ", ""), _EXPECTED_NO_FINGERPRINT),
)


def test_detector_matches_known_narrative_lines() -> None:
    """The detector fires on every known opening narrative and not on progress."""
    for line, expected in _NARRATIVE_CASES:
        detected = is_restart_narrative(line)
        assert detected is expected, (
            f"{line!r} must be detected as a restart narrative: {expected}; got {detected}"
        )


def test_known_pattern_set_is_fully_exercised() -> None:
    """Every pattern in the known set matches at least one realistic line.

    A pattern nobody exercises is a pattern the detector can never fire
    on, so the table above must cover the whole compiled set.
    """
    matched: set[re.Pattern[str]] = set()
    for line, _expected in _NARRATIVE_CASES:
        normalized = line.strip().lower()
        for pattern in _RESTART_PATTERNS:
            if pattern.match(normalized):
                matched.add(pattern)

    uncovered = sorted(pattern.pattern for pattern in _RESTART_PATTERNS - matched)
    assert not uncovered, (
        f"every known restart pattern must be matched by at least one "
        f"realistic line; uncovered patterns: {uncovered}"
    )


def test_structural_fingerprint_collapses_to_the_expected_value() -> None:
    """Every rendered output sequence maps to its exact structural fingerprint."""
    for output, expected in _FINGERPRINT_CASES:
        fingerprint = structural_restart_fingerprint(output)
        assert fingerprint == expected, (
            f"output {list(output)!r} must collapse to {expected!r}; got {fingerprint!r}"
        )


def test_restart_pattern_set_is_a_nonempty_frozenset() -> None:
    """The pattern set is a non-empty, immutable module-level frozenset.

    This is the import-time invariant guard's runtime check: an empty
    pattern set would silently disable the structural detector. The
    emptiness check is expressed as a CONTENT check (a compiled pattern
    actually matches the canonical narrative) rather than a length check,
    so it pins the behaviour instead of the cardinality.
    """
    assert isinstance(_RESTART_PATTERNS, frozenset)
    # The set should be a module-level singleton; assert identity is stable
    # across two reads.
    assert _restart_module._RESTART_PATTERNS is _RESTART_PATTERNS
    assert any(
        pattern.match(_CANONICAL_RESTART_LINE.strip().lower()) for pattern in _RESTART_PATTERNS
    ), "the canonical restart narrative must be matched by the compiled pattern set"
