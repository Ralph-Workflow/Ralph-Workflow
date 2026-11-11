from __future__ import annotations

from unittest.mock import patch

from ralph.exit_pause import (
    LaunchContext,
    PauseOnExitMode,
    exit_pause,
)


class TestExitPause:
    def test_exit_pause_never_does_not_raise(self) -> None:
        with patch("ralph.exit_pause.detect_launch_context") as mock_detect:
            mock_detect.return_value = LaunchContext(
                is_windows=True,
                has_terminal_session_marker=False,
                parent_process_name="explorer.exe",
            )
            exit_pause(PauseOnExitMode.NEVER)

    def test_exit_pause_auto_no_pause_does_not_raise(self) -> None:
        with patch("ralph.exit_pause.detect_launch_context") as mock_detect:
            mock_detect.return_value = LaunchContext(
                is_windows=False,
                has_terminal_session_marker=False,
                parent_process_name=None,
            )
            exit_pause(PauseOnExitMode.AUTO)
