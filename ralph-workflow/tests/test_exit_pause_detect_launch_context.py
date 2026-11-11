from __future__ import annotations

from unittest.mock import patch

from ralph.exit_pause import (
    LaunchContext,
    detect_launch_context,
)


class TestDetectLaunchContext:
    def test_detect_launch_context_returns_launch_context(self) -> None:
        ctx = detect_launch_context()
        assert isinstance(ctx, LaunchContext)
        assert isinstance(ctx.is_windows, bool)
        assert isinstance(ctx.has_terminal_session_marker, bool)

    def test_terminal_marker_detected(self) -> None:
        ctx = detect_launch_context(env={"WT_SESSION": "test-session"})
        assert ctx.has_terminal_session_marker is True

    def test_no_marker_when_env_empty(self) -> None:
        ctx = detect_launch_context(env={})
        assert ctx.has_terminal_session_marker is False

    def test_marker_detected_via_term_env_var(self) -> None:
        ctx = detect_launch_context(env={"WT_SESSION": "test-123"})
        assert ctx.has_terminal_session_marker is True

    @patch("ralph.exit_pause.sys.platform", "win32")
    def test_is_windows_detected_from_sys_platform(self) -> None:
        with patch("ralph.exit_pause.os.name", "nt"):
            ctx = detect_launch_context()
            assert ctx.is_windows is True

    @patch("ralph.exit_pause.sys.platform", "linux")
    def test_is_windows_false_on_linux(self) -> None:
        with patch("ralph.exit_pause.os.name", "posix"):
            ctx = detect_launch_context()
            assert ctx.is_windows is False
