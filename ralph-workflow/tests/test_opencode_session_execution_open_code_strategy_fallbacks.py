from __future__ import annotations

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import (
    AgentExecutionState,
    OpenCodeExecutionStrategy,
)
from tests.fake_handle import _FakeHandle

_DESCENDANT_WAIT_POLL_SECONDS = 0.5


class TestOpenCodeStrategyFallbacks:
    def test_classify_quiet_probe_exception_falls_back_to_descendants(self) -> None:
        class _RaisingProbe:
            def any_agent_active(self, label_prefix: str) -> bool:
                del label_prefix
                raise RuntimeError("boom")

        strategy = OpenCodeExecutionStrategy(label_scope="run-scope")
        handle = _FakeHandle(has_descendants=True)

        state = strategy.classify_quiet(handle, _RaisingProbe())

        assert state == AgentExecutionState.WAITING_ON_CHILD

    def test_classify_exit_probe_exception_falls_back_to_descendants(self) -> None:
        class _RaisingProbe:
            def any_agent_active(self, label_prefix: str) -> bool:
                del label_prefix
                raise RuntimeError("boom")

        strategy = OpenCodeExecutionStrategy(label_scope="run-scope")
        handle = _FakeHandle(returncode=0, has_descendants=True)
        signals = CompletionSignals(False, False, ())

        state = strategy.classify_exit(
            handle,
            signals,
            liveness_probe=_RaisingProbe(),
        )

        assert state == AgentExecutionState.WAITING_ON_CHILD

    def test_classify_exit_probe_exception_without_descendants_is_resumable(self) -> None:
        class _RaisingProbe:
            def any_agent_active(self, label_prefix: str) -> bool:
                del label_prefix
                raise RuntimeError("boom")

        strategy = OpenCodeExecutionStrategy(label_scope="run-scope")
        handle = _FakeHandle(returncode=0, has_descendants=False)
        signals = CompletionSignals(False, False, ())

        state = strategy.classify_exit(
            handle,
            signals,
            liveness_probe=_RaisingProbe(),
        )

        assert state == AgentExecutionState.RESUMABLE_CONTINUE
