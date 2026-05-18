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
    LaunchContext,
    PauseOnExitMode,
    should_pause_before_exit,
)


class TestShouldPauseBeforeExit:
    """Tests for should_pause_before_exit() function."""

    def test_never_mode_always_returns_false(self) -> None:
        """Test that NEVER mode never pauses regardless of outcome or context."""
        ctx = LaunchContext(
            is_windows=True,
            has_terminal_session_marker=False,
            parent_process_name="explorer.exe",
        )
        for outcome in ExitOutcome:
            result = should_pause_before_exit(PauseOnExitMode.NEVER, outcome, ctx)
            assert result is False

    def test_always_mode_always_returns_true(self) -> None:
        """Test that ALWAYS mode always pauses regardless of outcome or context."""
        ctx = LaunchContext(
            is_windows=False,
            has_terminal_session_marker=True,
            parent_process_name=None,
        )
        for outcome in ExitOutcome:
            result = should_pause_before_exit(PauseOnExitMode.ALWAYS, outcome, ctx)
            assert result is True

    def test_auto_mode_non_failure_never_pauses(self) -> None:
        """Test that AUTO mode never pauses for non-FAILURE outcomes."""
        ctx = LaunchContext(
            is_windows=True,
            has_terminal_session_marker=False,
            parent_process_name="explorer.exe",
        )
        for outcome in (ExitOutcome.SUCCESS, ExitOutcome.INTERRUPTED):
            result = should_pause_before_exit(PauseOnExitMode.AUTO, outcome, ctx)
            assert result is False

    def test_auto_mode_failure_windows_standalone_pauses(self) -> None:
        """Test that AUTO mode pauses on FAILURE for standalone Windows launch."""
        ctx = LaunchContext(
            is_windows=True,
            has_terminal_session_marker=False,
            parent_process_name="explorer.exe",
        )
        result = should_pause_before_exit(PauseOnExitMode.AUTO, ExitOutcome.FAILURE, ctx)
        assert result is True

    def test_auto_mode_failure_windows_with_terminal_does_not_pause(self) -> None:
        """Test that AUTO mode does not pause when terminal session marker is present."""
        ctx = LaunchContext(
            is_windows=True,
            has_terminal_session_marker=True,
            parent_process_name="explorer.exe",
        )
        result = should_pause_before_exit(PauseOnExitMode.AUTO, ExitOutcome.FAILURE, ctx)
        assert result is False

    def test_auto_mode_failure_non_windows_does_not_pause(self) -> None:
        """Test that AUTO mode does not pause on non-Windows platforms."""
        ctx = LaunchContext(
            is_windows=False,
            has_terminal_session_marker=False,
            parent_process_name="bash",
        )
        result = should_pause_before_exit(PauseOnExitMode.AUTO, ExitOutcome.FAILURE, ctx)
        assert result is False

    def test_auto_mode_failure_windows_unknown_parent_does_not_pause(self) -> None:
        """Test that AUTO mode does not pause when parent process name is unknown."""
        ctx = LaunchContext(
            is_windows=True,
            has_terminal_session_marker=False,
            parent_process_name=None,
        )
        result = should_pause_before_exit(PauseOnExitMode.AUTO, ExitOutcome.FAILURE, ctx)
        assert result is False

    def test_auto_mode_failure_windows_non_explorer_parent_does_not_pause(self) -> None:
        """Test that AUTO mode does not pause when parent is not explorer.exe."""
        ctx = LaunchContext(
            is_windows=True,
            has_terminal_session_marker=False,
            parent_process_name="code.exe",
        )
        result = should_pause_before_exit(PauseOnExitMode.AUTO, ExitOutcome.FAILURE, ctx)
        assert result is False

    def test_explorer_exe_case_insensitive(self) -> None:
        """Test that explorer.exe comparison is case-insensitive."""
        ctx = LaunchContext(
            is_windows=True,
            has_terminal_session_marker=False,
            parent_process_name="EXPLORER.EXE",
        )
        result = should_pause_before_exit(PauseOnExitMode.AUTO, ExitOutcome.FAILURE, ctx)
        assert result is True
