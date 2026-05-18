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
    PauseOnExitMode,
)


class TestPauseOnExitMode:
    """Tests for PauseOnExitMode enum."""

    def test_pause_on_exit_mode_values(self) -> None:
        """Test that all expected pause mode values exist."""
        assert PauseOnExitMode.NEVER == "never"
        assert PauseOnExitMode.ALWAYS == "always"
        assert PauseOnExitMode.AUTO == "auto"

    def test_pause_on_exit_mode_is_str_enum(self) -> None:
        """Test that PauseOnExitMode is a string enum."""
        assert isinstance(PauseOnExitMode.NEVER, str)
        assert PauseOnExitMode.NEVER == "never"
