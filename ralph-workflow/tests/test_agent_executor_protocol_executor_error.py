from __future__ import annotations

from ralph.agents.executor import ExecutorError

DURATION_MS = 123


class TestExecutorError:
    def test_is_exception(self) -> None:
        err = ExecutorError("something went wrong")
        assert isinstance(err, Exception)
        assert str(err) == "something went wrong"
