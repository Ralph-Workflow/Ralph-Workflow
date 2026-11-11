from __future__ import annotations

from ralph.exit_pause import (
    ExitOutcome,
    LaunchContext,
    PauseOnExitMode,
    should_pause_before_exit,
)


class TestShouldPauseBeforeExit:
    def test_never_mode_always_returns_false(self) -> None:
        ctx = LaunchContext(
            is_windows=True,
            has_terminal_session_marker=False,
            parent_process_name="explorer.exe",
        )
        for outcome in ExitOutcome:
            result = should_pause_before_exit(PauseOnExitMode.NEVER, outcome, ctx)
            assert result is False

    def test_always_mode_always_returns_true(self) -> None:
        ctx = LaunchContext(
            is_windows=False,
            has_terminal_session_marker=True,
            parent_process_name=None,
        )
        for outcome in ExitOutcome:
            result = should_pause_before_exit(PauseOnExitMode.ALWAYS, outcome, ctx)
            assert result is True

    def test_auto_mode_non_failure_never_pauses(self) -> None:
        ctx = LaunchContext(
            is_windows=True,
            has_terminal_session_marker=False,
            parent_process_name="explorer.exe",
        )
        for outcome in (ExitOutcome.SUCCESS, ExitOutcome.INTERRUPTED):
            result = should_pause_before_exit(PauseOnExitMode.AUTO, outcome, ctx)
            assert result is False

    def test_auto_mode_failure_windows_standalone_pauses(self) -> None:
        ctx = LaunchContext(
            is_windows=True,
            has_terminal_session_marker=False,
            parent_process_name="explorer.exe",
        )
        result = should_pause_before_exit(PauseOnExitMode.AUTO, ExitOutcome.FAILURE, ctx)
        assert result is True

    def test_auto_mode_failure_windows_with_terminal_does_not_pause(self) -> None:
        ctx = LaunchContext(
            is_windows=True,
            has_terminal_session_marker=True,
            parent_process_name="explorer.exe",
        )
        result = should_pause_before_exit(PauseOnExitMode.AUTO, ExitOutcome.FAILURE, ctx)
        assert result is False

    def test_auto_mode_failure_non_windows_does_not_pause(self) -> None:
        ctx = LaunchContext(
            is_windows=False,
            has_terminal_session_marker=False,
            parent_process_name="bash",
        )
        result = should_pause_before_exit(PauseOnExitMode.AUTO, ExitOutcome.FAILURE, ctx)
        assert result is False

    def test_auto_mode_failure_windows_unknown_parent_does_not_pause(self) -> None:
        ctx = LaunchContext(
            is_windows=True,
            has_terminal_session_marker=False,
            parent_process_name=None,
        )
        result = should_pause_before_exit(PauseOnExitMode.AUTO, ExitOutcome.FAILURE, ctx)
        assert result is False

    def test_auto_mode_failure_windows_non_explorer_parent_does_not_pause(self) -> None:
        ctx = LaunchContext(
            is_windows=True,
            has_terminal_session_marker=False,
            parent_process_name="code.exe",
        )
        result = should_pause_before_exit(PauseOnExitMode.AUTO, ExitOutcome.FAILURE, ctx)
        assert result is False

    def test_explorer_exe_case_insensitive(self) -> None:
        ctx = LaunchContext(
            is_windows=True,
            has_terminal_session_marker=False,
            parent_process_name="EXPLORER.EXE",
        )
        result = should_pause_before_exit(PauseOnExitMode.AUTO, ExitOutcome.FAILURE, ctx)
        assert result is True
