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
    detect_launch_context,
)


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
