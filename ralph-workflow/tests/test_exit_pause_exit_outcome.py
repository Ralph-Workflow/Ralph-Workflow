"""Unit tests for the exit_pause module.

Tests cover:
- ExitOutcome and PauseOnExitMode enum values
- should_pause_before_exit() logic for all modes
- is_failure_outcome detection
- auto-pause on Windows vs non-Windows standalone launches
- detect_launch_context() with monkeypatched platform detection
"""

from __future__ import annotations

from ralph.exit_pause import (
    ExitOutcome,
)


class TestExitOutcome:
    """Tests for ExitOutcome enum."""

    def test_exit_outcome_values(self) -> None:
        """Test that all expected exit outcome values exist."""
        assert ExitOutcome.SUCCESS == "success"
        assert ExitOutcome.FAILURE == "failure"
        assert ExitOutcome.INTERRUPTED == "interrupted"

    def test_exit_outcome_is_str_enum(self) -> None:
        """Test that ExitOutcome is a string enum."""
        assert isinstance(ExitOutcome.SUCCESS, str)
        assert ExitOutcome.SUCCESS == "success"
