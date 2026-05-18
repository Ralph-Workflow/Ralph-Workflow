from __future__ import annotations

import inspect

from ralph.agents.executor import AgentExecutor, WorkerResult
from ralph.pipeline.work_units import WorkUnit

DURATION_MS = 123


class TestAgentExecutorProtocol:
    def test_protocol_is_runtime_checkable(self) -> None:
        assert isinstance(AgentExecutor, type)

    def test_run_method_has_correct_params(self) -> None:
        sig = inspect.signature(AgentExecutor.run)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "unit" in params
        assert "on_output" in params
        assert "on_status" in params

    def test_run_method_unit_annotation(self) -> None:
        sig = inspect.signature(AgentExecutor.run)
        assert sig.parameters["unit"].annotation is WorkUnit

    def test_mock_implementor_passes_isinstance(self) -> None:
        class FakeExecutor:
            async def run(
                self,
                unit: WorkUnit,
                *,
                on_output: object,
                on_status: object,
            ) -> WorkerResult:
                return WorkerResult(
                    unit_id=unit.unit_id,
                    exit_code=0,
                    final_message="ok",
                    duration_ms=0,
                )

        fake = FakeExecutor()
        assert isinstance(fake, AgentExecutor)

    def test_class_without_run_fails_isinstance(self) -> None:
        class NotAnExecutor:
            pass

        assert not isinstance(NotAnExecutor(), AgentExecutor)
