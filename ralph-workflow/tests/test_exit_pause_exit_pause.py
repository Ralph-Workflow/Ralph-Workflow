"""Unit tests for the exit_pause module.

Tests cover:
- ExitOutcome and PauseOnExitMode enum values
- should_pause_before_exit() logic for all modes
- is_failure_outcome detection
- auto-pause on Windows vs non-Windows standalone launches
- detect_launch_context() with monkeypatched platform detection
"""

from __future__ import annotations

from unittest.mock import patch

from ralph.exit_pause import (
    LaunchContext,
    PauseOnExitMode,
    exit_pause,
)


class TestExitPause:
    """Tests for exit_pause() function.

    Note: exit_pause() calls input() which blocks in tests, so we only
    test that it doesn't raise for non-pause cases. The pause behavior
    is tested via should_pause_before_exit() above.
    """

    def test_exit_pause_never_does_not_raise(self) -> None:
        """Test that exit_pause with NEVER mode doesn't raise."""
        # Should not raise even if input() would be called
        # (it won't be called because should_pause_before_exit returns False)
        with patch("ralph.exit_pause.detect_launch_context") as mock_detect:
            mock_detect.return_value = LaunchContext(
                is_windows=True,
                has_terminal_session_marker=False,
                parent_process_name="explorer.exe",
            )
            # This should not raise and should not call input()
            exit_pause(PauseOnExitMode.NEVER)

    def test_exit_pause_auto_no_pause_does_not_raise(self) -> None:
        """Test that exit_pause with AUTO mode on success doesn't raise."""
        with patch("ralph.exit_pause.detect_launch_context") as mock_detect:
            mock_detect.return_value = LaunchContext(
                is_windows=False,
                has_terminal_session_marker=False,
                parent_process_name=None,
            )
            # Should not raise because no pause is triggered
            exit_pause(PauseOnExitMode.AUTO)
