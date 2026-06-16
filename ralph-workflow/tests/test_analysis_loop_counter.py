"""Canonical analysis-loop counter semantics.

These tests pin the single source of truth for converting the raw stored
loopback count into every derived notion: the user-facing 1-based label, the
final-run flag, the re-entry skip flag, and the clamped next value. Historically
these conversions were duplicated across the display, reducer, and handoff code
with subtly different +/-1 conventions, which let the banner render an impossible
``cap + 1`` label (e.g. "Analysis 4/3"). The value object exists so the four
consumers can never disagree again.
"""

from __future__ import annotations

import pytest

from ralph.pipeline.progress import AnalysisLoopCounter


class TestDisplayIterationInvariant:
    """The user-facing label must always satisfy ``1 <= label <= cap``."""

    @pytest.mark.parametrize("cap", [1, 2, 3, 5, 10])
    def test_label_never_exceeds_cap_for_any_completed_count(self, cap: int) -> None:
        # Completed may transiently reach (and be clamped at) cap, the exhausted
        # sentinel. The label must saturate, never overflow to cap + 1.
        for completed in range(0, cap + 3):
            label = AnalysisLoopCounter(completed=completed, cap=cap).display_iteration
            assert 1 <= label <= cap, f"completed={completed} cap={cap} -> {label}"

    def test_exhausted_sentinel_does_not_overflow_label(self) -> None:
        """Regression for "Analysis 4/3": completed == cap renders as cap, not cap+1."""
        assert AnalysisLoopCounter(completed=3, cap=3).display_iteration == 3

    @pytest.mark.parametrize(
        ("completed", "cap", "expected"),
        [(0, 3, 1), (1, 3, 2), (2, 3, 3), (0, 1, 1)],
    )
    def test_label_is_one_based_in_normal_range(
        self, completed: int, cap: int, expected: int
    ) -> None:
        assert AnalysisLoopCounter(completed=completed, cap=cap).display_iteration == expected


class TestDerivedFlags:
    """is_final / should_skip_reentry / next_completed semantics."""

    @pytest.mark.parametrize(
        ("completed", "cap", "is_final", "should_skip"),
        [
            (0, 1, True, False),
            (0, 2, False, False),
            (1, 2, True, False),
            (2, 2, True, True),
            (0, 0, True, True),
        ],
    )
    def test_final_and_skip_flags(
        self, completed: int, cap: int, is_final: bool, should_skip: bool
    ) -> None:
        counter = AnalysisLoopCounter(completed=completed, cap=cap)
        assert counter.is_final is is_final
        assert counter.should_skip_reentry is should_skip

    @pytest.mark.parametrize(
        ("completed", "cap", "expected"),
        [(0, 3, 1), (2, 3, 3), (3, 3, 3), (5, 3, 3)],
    )
    def test_next_completed_clamps_at_cap(self, completed: int, cap: int, expected: int) -> None:
        assert AnalysisLoopCounter(completed=completed, cap=cap).next_completed == expected


class TestSkipVsFinalAtCap:
    """The skip flag must fire exactly one step after the final-run label.

    completed == cap-1 is the final *run* (label cap/cap); completed == cap is the
    exhausted state where the next entry must be skipped. This is the coupling that
    makes the loop terminate without ever showing an over-cap label.
    """

    @pytest.mark.parametrize("cap", [1, 2, 3])
    def test_skip_fires_one_step_after_final(self, cap: int) -> None:
        final_run = AnalysisLoopCounter(completed=cap - 1, cap=cap)
        assert final_run.is_final is True
        assert final_run.should_skip_reentry is False
        assert final_run.display_iteration == cap

        exhausted = AnalysisLoopCounter(completed=cap, cap=cap)
        assert exhausted.should_skip_reentry is True
        assert exhausted.display_iteration == cap
