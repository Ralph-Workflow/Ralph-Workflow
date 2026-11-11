from __future__ import annotations

from ralph.agents.activity import AgentActivityKind
from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import AgentExecutionState, BaseExecutionStrategy
from ralph.mcp.server import (
    reset_subagent_sink,
    set_subagent_sink,
)
from ralph.process.liveness import FakeLivenessProbe
from tests.fake_handle import _FakeHandle


class TestBaseExecutionStrategyDefaults:
    def test_classify_activity_line_empty_returns_none(self) -> None:
        strategy = BaseExecutionStrategy()
        assert strategy.classify_activity_line("") is None

    def test_classify_activity_line_non_blank_is_output_line(self) -> None:
        strategy = BaseExecutionStrategy()
        signal = strategy.classify_activity_line("hello world")
        assert signal is not None
        assert signal.kind == AgentActivityKind.OUTPUT_LINE

    def test_classify_quiet_returns_active_without_descendants(self) -> None:
        strategy = BaseExecutionStrategy()
        handle = _FakeHandle(has_descendants=False)
        probe = FakeLivenessProbe(active=False)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.ACTIVE

    def test_classify_quiet_returns_waiting_with_descendants(self) -> None:
        strategy = BaseExecutionStrategy()
        handle = _FakeHandle(has_descendants=True)
        probe = FakeLivenessProbe(active=False)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD

    def test_classify_exit_returns_terminal_complete(self) -> None:
        strategy = BaseExecutionStrategy()
        handle = _FakeHandle(returncode=0)
        signals = CompletionSignals(
            explicit_complete=False,
            required_artifact_present=False,
            artifact_types=(),
        )

        state = strategy.classify_exit(handle, signals)

        assert state == AgentExecutionState.TERMINAL_COMPLETE

    def test_supports_session_continuation_is_false(self) -> None:
        strategy = BaseExecutionStrategy()
        assert strategy.supports_session_continuation() is False

    def test_supports_completion_enforcement_is_false(self) -> None:
        strategy = BaseExecutionStrategy()
        assert strategy.supports_completion_enforcement() is False

    def test_observe_line_does_not_invoke_sink_without_signal(
        self,
    ) -> None:
        recorded: list[str] = []

        def _sink(line: str) -> None:
            recorded.append(line)

        token = set_subagent_sink(_sink)
        try:
            strategy = BaseExecutionStrategy()
            strategy.observe_line("hello world from the agent")
            assert recorded == [], (
                "observe_line on a non-child line MUST NOT invoke the"
                f" subagent sink; got {recorded!r}"
            )
        finally:
            reset_subagent_sink(token)

    def test_observe_line_invokes_sink_on_child_signal(self) -> None:
        recorded: list[str] = []

        def _sink(line: str) -> None:
            recorded.append(line)

        token = set_subagent_sink(_sink)
        try:
            strategy = BaseExecutionStrategy()
            strategy.observe_line("[child] do something")
            assert recorded == ["[child] do something"], (
                "observe_line on a [child] line MUST invoke the sink"
                f" exactly once; got {recorded!r}"
            )
        finally:
            reset_subagent_sink(token)
