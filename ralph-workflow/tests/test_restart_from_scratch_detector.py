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
"""

from __future__ import annotations

import pytest

import ralph.pipeline._restart_from_scratch as _restart_module
from ralph.pipeline._restart_from_scratch import (
    _RESTART_PATTERNS,
    is_restart_narrative,
    structural_restart_fingerprint,
)


def test_detector_flags_opening_narrative_patterns() -> None:
    """The canonical restart line from the PROMPT.md bug log is detected."""
    line = (
        "I will start by reading the current state of the task, the "
        "analysis feedback, and the existing plan draft in parallel."
    )
    assert is_restart_narrative(line) is True


def test_detector_does_not_flag_genuine_progress() -> None:
    """A genuine progress line is NOT detected as a restart narrative."""
    line = "I have completed step 1 and am moving to step 2."
    assert is_restart_narrative(line) is False


def test_detector_is_case_insensitive() -> None:
    """The detector matches uppercase and lowercase renderings of the same line."""
    assert is_restart_narrative("I WILL START BY READING THE PROMPT") is True
    assert is_restart_narrative("i will start by reading the prompt") is True
    assert is_restart_narrative("I Will Start By Reading The Prompt") is True


def test_detector_collapses_different_concrete_tokens() -> None:
    """Different concrete tokens in the same opening-narrative line all flag.

    The literal fingerprint (RepetitionTracker.fingerprint + _NUMERIC_TOKEN)
    does NOT collapse path tokens like /tmp/PROMPT.md vs /tmp/PROMPT_v2.md —
    the chars beyond the numeric vocabulary diverge. The structural detector
    is the safety net that catches this case.
    """
    candidates = [
        "I will start by reading the prompt at /tmp/PROMPT.md",
        "I will start by reading the prompt at /tmp/PROMPT_v2.md",
        "I will start by reading the prompt at /tmp/PROMPT_draft.md",
        "I will start by reading the prompt at /Users/me/PROMPT-2026.md",
    ]
    results = [is_restart_narrative(line) for line in candidates]
    assert all(results), (
        "every opening-narrative variant must be detected, regardless of "
        f"concrete tokens; got {results} for {candidates}"
    )


@pytest.mark.parametrize(
    "line",
    [
        "I will start by reading the prompt.",
        "First, let me read the current state.",
        "First let me read the prompt.",
        "Let me first read the current task.",
        "Let me read the current prompt.",
        "I need to read the current state of the task.",
        "I need to read the task description.",
        "I will start by re-reading the prompt.",
        "I will start by reading the analysis feedback.",
        "Starting by reading the prompt file.",
        "Starting from scratch with the prompt.",
        "Starting from the beginning again.",
        "Let me begin by reading the plan.",
        "Let me begin from reading the spec.",
        "Let me begin with reading the requirements.",
        "Re-reading the prompt.",
        "Re-reading the task description.",
    ],
)
def test_detector_matches_each_known_pattern(line: str) -> None:
    """Every pattern in the known set matches at least one realistic line."""
    assert is_restart_narrative(line) is True


def test_structural_fingerprint_collapses_different_concrete_tokens() -> None:
    """All structurally-similar opening narratives produce the same fingerprint.

    This is the property the zero-progress guard consumes: when the literal
    signature drifts (different concrete file names), the structural
    fingerprint MUST collapse to the same value so the guard fires.
    """
    outputs = [
        ["I will start by reading the prompt at /tmp/PROMPT.md"],
        ["I will start by reading the prompt at /tmp/PROMPT_v2.md"],
        ["I will start by reading the prompt at /tmp/PROMPT_draft.md"],
    ]
    fingerprints = [structural_restart_fingerprint(out) for out in outputs]
    assert len(set(fingerprints)) == 1, (
        f"all three opening-narrative outputs must collapse to one structural "
        f"fingerprint; got {fingerprints}"
    )


def test_structural_fingerprint_returns_restart_none_for_no_pattern() -> None:
    """If no opening-narrative line is found, the fingerprint is the
    ``restart:none`` sentinel. Callers (the zero-progress guard) treat this
    as "no structural signal" and fall back to the literal fingerprint.
    """
    outputs = [
        ["All good, made progress on the task today"],
        ["Completed step 3 of the plan, will continue with step 4"],
    ]
    for out in outputs:
        assert structural_restart_fingerprint(out) == "restart:none"


def test_structural_fingerprint_picks_first_matching_line() -> None:
    """The fingerprint is built from the FIRST matched line, not the last.

    The detector only collapses the first matched opening-narrative
    pattern; subsequent lines do not change the fingerprint. This makes
    the function deterministic and means a long agent transcript with
    a single restart opener is still recognized.
    """
    out = [
        "All good progress notes",
        "I will start by reading the prompt",
        "Now reading file /etc/PROMPT.md",
    ]
    fp1 = structural_restart_fingerprint(out)
    out2 = [
        "All good progress notes",
        "I will start by reading the prompt",
        "Now reading file /etc/PROMPT_v9.md",
    ]
    fp2 = structural_restart_fingerprint(out2)
    assert fp1 == fp2


def test_structural_fingerprint_ignores_lines_after_match() -> None:
    """Lines after the first match do not change the fingerprint."""
    out_a = [
        "I will start by reading the prompt",
        "subsequent line A",
    ]
    out_b = [
        "I will start by reading the prompt",
        "completely different second line B",
    ]
    assert structural_restart_fingerprint(out_a) == structural_restart_fingerprint(out_b)


def test_structural_fingerprint_handles_empty_input() -> None:
    """An empty output sequence returns the ``restart:none`` sentinel."""
    assert structural_restart_fingerprint([]) == "restart:none"


def test_structural_fingerprint_handles_lines_without_strings() -> None:
    """A sequence of empty strings does not raise and returns the sentinel."""
    assert structural_restart_fingerprint(["", "  ", ""]) == "restart:none"


def test_restart_pattern_set_is_nonempty() -> None:
    """The pattern set is non-empty and is a frozenset.

    This is the import-time invariant guard's runtime check: an empty
    pattern set would silently disable the structural detector.
    """
    assert isinstance(_RESTART_PATTERNS, frozenset)
    assert len(_RESTART_PATTERNS) > 0


def test_restart_pattern_set_is_frozenset_at_runtime() -> None:
    """Even after use, the set is still a frozenset (immutable contract)."""
    assert isinstance(_RESTART_PATTERNS, frozenset)
    # The set should be a module-level singleton; assert identity is stable
    # across two reads.
    assert _restart_module._RESTART_PATTERNS is _RESTART_PATTERNS
