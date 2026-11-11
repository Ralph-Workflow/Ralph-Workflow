from __future__ import annotations

from ralph.exit_pause import (
    PauseOnExitMode,
)


class TestPauseOnExitMode:
    def test_pause_on_exit_mode_values(self) -> None:
        assert PauseOnExitMode.NEVER == "never"
        assert PauseOnExitMode.ALWAYS == "always"
        assert PauseOnExitMode.AUTO == "auto"

    def test_pause_on_exit_mode_is_str_enum(self) -> None:
        assert isinstance(PauseOnExitMode.NEVER, str)
        assert PauseOnExitMode.NEVER == "never"
