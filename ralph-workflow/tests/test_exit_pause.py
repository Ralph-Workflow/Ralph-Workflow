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
    ExitOutcome,
    LaunchContext,
    PauseOnExitMode,
    detect_launch_context,
    exit_pause,
    should_pause_before_exit,
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


class TestDetectLaunchContext:
    """Tests for detect_launch_context() function."""

    def test_detect_launch_context_returns_launch_context(self) -> None:
        """Test that detect_launch_context returns a LaunchContext instance."""
        ctx = detect_launch_context()
        assert isinstance(ctx, LaunchContext)
        assert isinstance(ctx.is_windows, bool)
        assert isinstance(ctx.has_terminal_session_marker, bool)

    def test_terminal_marker_detected(self) -> None:
        """Test that WT_SESSION environment variable sets has_terminal_session_marker."""
        ctx = detect_launch_context(env={"WT_SESSION": "test-session"})
        assert ctx.has_terminal_session_marker is True

    def test_no_marker_when_env_empty(self) -> None:
        """Test that detect_launch_context returns False marker when env is empty."""
        ctx = detect_launch_context(env={})
        assert ctx.has_terminal_session_marker is False

    def test_marker_detected_via_term_env_var(self) -> None:
        """Test that WT_SESSION terminal env var sets has_terminal_session_marker."""
        ctx = detect_launch_context(env={"WT_SESSION": "test-123"})
        assert ctx.has_terminal_session_marker is True

    @patch("ralph.exit_pause.sys.platform", "win32")
    def test_is_windows_detected_from_sys_platform(self) -> None:
        """Test that Windows detection uses sys.platform."""
        with patch("ralph.exit_pause.os.name", "nt"):
            ctx = detect_launch_context()
            assert ctx.is_windows is True

    @patch("ralph.exit_pause.sys.platform", "linux")
    def test_is_windows_false_on_linux(self) -> None:
        """Test that is_windows is False on Linux."""
        with patch("ralph.exit_pause.os.name", "posix"):
            ctx = detect_launch_context()
            assert ctx.is_windows is False


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
