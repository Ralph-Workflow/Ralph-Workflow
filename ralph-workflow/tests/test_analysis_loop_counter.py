from __future__ import annotations

import pytest

from ralph.pipeline.progress import AnalysisLoopCounter


class TestDisplayIterationInvariant:
    @pytest.mark.parametrize("cap", [1, 2, 3, 5, 10])
    def test_label_never_exceeds_cap_for_any_completed_count(self, cap: int) -> None:
        for completed in range(0, cap + 3):
            label = AnalysisLoopCounter(completed=completed, cap=cap).display_iteration
            assert 1 <= label <= cap, f"completed={completed} cap={cap} -> {label}"

    def test_exhausted_sentinel_does_not_overflow_label(self) -> None:
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
    @pytest.mark.parametrize("cap", [1, 2, 3])
    def test_skip_fires_one_step_after_final(self, cap: int) -> None:
        final_run = AnalysisLoopCounter(completed=cap - 1, cap=cap)
        assert final_run.is_final is True
        assert final_run.should_skip_reentry is False
        assert final_run.display_iteration == cap

        exhausted = AnalysisLoopCounter(completed=cap, cap=cap)
        assert exhausted.should_skip_reentry is True
        assert exhausted.display_iteration == cap
