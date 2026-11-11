from __future__ import annotations

from ralph.exit_pause import (
    ExitOutcome,
)


class TestExitOutcome:
    def test_exit_outcome_values(self) -> None:
        assert ExitOutcome.SUCCESS == "success"
        assert ExitOutcome.FAILURE == "failure"
        assert ExitOutcome.INTERRUPTED == "interrupted"

    def test_exit_outcome_is_str_enum(self) -> None:
        assert isinstance(ExitOutcome.SUCCESS, str)
        assert ExitOutcome.SUCCESS == "success"
