from __future__ import annotations

import re

import ralph.pipeline._restart_from_scratch as _restart_module
from ralph.pipeline._restart_from_scratch import (
    _RESTART_PATTERNS,
    is_restart_narrative,
)

_CANONICAL_RESTART_LINE = (
    "I will start by reading the current state of the task, the "
    "analysis feedback, and the existing plan draft in parallel."
)

_NARRATIVE_CASES: tuple[tuple[str, bool], ...] = (
    (_CANONICAL_RESTART_LINE, True),
    ("I have completed step 1 and am moving to step 2.", False),
    ("I WILL START BY READING THE PROMPT", True),
    ("i will start by reading the prompt", True),
    ("I Will Start By Reading The Prompt", True),
    ("I will start by reading the prompt at /tmp/PROMPT.md", True),
    ("I will start by reading the prompt at /tmp/PROMPT_v2.md", True),
    ("I will start by reading the prompt at /tmp/PROMPT_draft.md", True),
    ("I will start by reading the prompt at /Users/me/PROMPT-2026.md", True),
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


def test_detector_matches_known_narrative_lines() -> None:
    for line, expected in _NARRATIVE_CASES:
        detected = is_restart_narrative(line)
        assert detected is expected, (
            f"{line!r} must be detected as a restart narrative: {expected}; got {detected}"
        )


def test_known_pattern_set_is_fully_exercised() -> None:
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


def test_restart_pattern_set_is_a_nonempty_frozenset() -> None:
    assert isinstance(_RESTART_PATTERNS, frozenset)
    assert _restart_module._RESTART_PATTERNS is _RESTART_PATTERNS
    assert any(
        pattern.match(_CANONICAL_RESTART_LINE.strip().lower()) for pattern in _RESTART_PATTERNS
    ), "the canonical restart narrative must be matched by the compiled pattern set"
