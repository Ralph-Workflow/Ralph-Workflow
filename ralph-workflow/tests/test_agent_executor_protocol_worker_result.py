from __future__ import annotations

import pytest

from ralph.agents.executor import WorkerResult

DURATION_MS = 123


class TestWorkerResult:
    def test_fields_accessible(self) -> None:
        result = WorkerResult(
            unit_id="u1",
            exit_code=0,
            final_message="done",
            duration_ms=DURATION_MS,
        )
        assert result.unit_id == "u1"
        assert result.exit_code == 0
        assert result.final_message == "done"
        assert result.duration_ms == DURATION_MS

    def test_frozen_raises_on_mutation(self) -> None:
        result = WorkerResult(
            unit_id="u1",
            exit_code=0,
            final_message="done",
            duration_ms=0,
        )
        with pytest.raises((AttributeError, TypeError)):
            result.exit_code = 1
