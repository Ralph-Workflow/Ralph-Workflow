from __future__ import annotations

import pytest

from ralph.agents.activity import AgentActivityKind
from ralph.agents.execution_state import (
    BaseExecutionStrategy,
    ClaudeExecutionStrategy,
    ClaudeInteractiveExecutionStrategy,
    GenericExecutionStrategy,
    OpenCodeExecutionStrategy,
    strategy_for_command,
    strategy_for_transport,
)
from ralph.agents.execution_state._factory import _make_agy_strategy
from ralph.agents.execution_state._helpers import _classify_generic_child_signal
from ralph.config.enums import AgentTransport
from ralph.mcp.server._activity_sink import (
    reset_subagent_sink,
    set_subagent_sink,
)


@pytest.fixture
def subagent_sink_spy() -> list[str]:
    recorded: list[str] = []

    def _record(line: str) -> None:
        recorded.append(line)

    token = set_subagent_sink(_record)
    try:
        yield recorded
    finally:
        reset_subagent_sink(token)


class TestClassifyGenericChildSignal:
    def test_classify_generic_child_signal_matches_opencode_json(self) -> None:

        signal = _classify_generic_child_signal('{"type":"child_progress","msg":"thinking"}')
        assert signal is not None
        assert signal.kind == AgentActivityKind.CHILD_PROGRESS

    def test_classify_generic_child_signal_matches_claude_prefixed(self) -> None:

        signal = _classify_generic_child_signal("[child] do something")
        assert signal is not None
        assert signal.kind == AgentActivityKind.CHILD_PROGRESS

    def test_classify_generic_child_signal_matches_subagent_heartbeat(self) -> None:

        signal = _classify_generic_child_signal("Subagent heartbeat at t=30s")
        assert signal is not None
        assert signal.kind == AgentActivityKind.CHILD_HEARTBEAT

    def test_classify_generic_child_signal_matches_codex_json_event(self) -> None:
        signal = _classify_generic_child_signal('{"event":"child_progress","data":"thinking"}')
        assert signal is not None
        assert signal.kind == AgentActivityKind.CHILD_PROGRESS

    def test_classify_generic_child_signal_does_not_classify_bare_progress_event(self) -> None:
        signal = _classify_generic_child_signal('{"event":"progress","data":"thinking"}')
        assert signal is None, (
            f"bare progress event MUST NOT classify as child activity"
            f" per analysis-feedback fix, got {signal!r}"
        )

    def test_classify_generic_child_signal_does_not_classify_bare_tool_call_event(self) -> None:
        signal = _classify_generic_child_signal(
            '{"type":"tool_call","name":"bash","args":{"cmd":"ls"}}'
        )
        assert signal is None, (
            f"bare tool_call event MUST NOT classify as child activity"
            f" per analysis-feedback fix, got {signal!r}"
        )

    def test_classify_generic_child_signal_does_not_classify_bare_heartbeat_event(self) -> None:
        signal = _classify_generic_child_signal('{"type":"heartbeat","ts":1234567890}')
        assert signal is None, (
            f"bare heartbeat event MUST NOT classify as child activity"
            f" per analysis-feedback fix, got {signal!r}"
        )

    def test_classify_generic_child_signal_does_not_classify_bare_alive_event(self) -> None:
        signal = _classify_generic_child_signal('{"event":"alive","ts":1234567890}')
        assert signal is None, (
            f"bare alive event MUST NOT classify as child activity"
            f" per analysis-feedback fix, got {signal!r}"
        )

    def test_classify_generic_child_signal_does_not_classify_task_progress_event(self) -> None:
        signal = _classify_generic_child_signal('{"event":"task_progress","data":"thinking"}')
        assert signal is None, (
            f"bare task_progress event MUST NOT classify as child activity"
            f" per analysis-feedback fix, got {signal!r}"
        )

    def test_classify_generic_child_signal_matches_subagent_scoped_prefix(self) -> None:
        signal = _classify_generic_child_signal(
            '{"type":"subagent_progress_phase1","data":"thinking"}'
        )
        assert signal is not None
        assert signal.kind == AgentActivityKind.CHILD_PROGRESS

    def test_classify_generic_child_signal_matches_child_scoped_prefix(self) -> None:
        signal = _classify_generic_child_signal(
            '{"type":"child_progress_phase1","data":"thinking"}'
        )
        assert signal is not None
        assert signal.kind == AgentActivityKind.CHILD_PROGRESS

    def test_classify_generic_child_signal_matches_subagent_heartbeat_prefix(self) -> None:
        signal = _classify_generic_child_signal(
            '{"type":"subagent_heartbeat_extra","data":"thinking"}'
        )
        assert signal is not None
        assert signal.kind == AgentActivityKind.CHILD_PROGRESS

    def test_classify_generic_child_signal_returns_none_for_terminal_event(self) -> None:
        signal = _classify_generic_child_signal('{"type":"child_complete","child_id":"abc"}')
        assert signal is None

    def test_classify_generic_child_signal_returns_none_for_unrelated_line(self) -> None:

        signal = _classify_generic_child_signal("hello world from the agent")
        assert signal is None

    def test_classify_generic_child_signal_returns_none_for_empty_line(self) -> None:

        assert _classify_generic_child_signal("") is None
        assert _classify_generic_child_signal("   ") is None
        assert _classify_generic_child_signal("\n") is None


class TestClassifyGenericChildSignalAnalysisFeedbackRegressions:
    def test_subagent_failed_json_returns_none(self) -> None:

        signal = _classify_generic_child_signal('{"type":"subagent_failed","child_id":"abc"}')
        assert signal is None, (
            f"subagent_failed MUST classify to None (terminal event, not progress), got {signal!r}"
        )

    def test_subagent_cancelled_json_returns_none(self) -> None:

        signal = _classify_generic_child_signal('{"event":"subagent_cancelled","child_id":"abc"}')
        assert signal is None, (
            f"subagent_cancelled MUST classify to None (terminal event,"
            f" not progress), got {signal!r}"
        )

    def test_subagent_terminal_json_returns_none(self) -> None:

        signal = _classify_generic_child_signal('{"type":"subagent_terminal","child_id":"abc"}')
        assert signal is None, (
            f"subagent_terminal MUST classify to None (terminal event), got {signal!r}"
        )

    def test_child_failed_json_returns_none(self) -> None:

        signal = _classify_generic_child_signal('{"event":"child_failed","child_id":"abc"}')
        assert signal is None, (
            f"child_failed MUST classify to None (terminal event), got {signal!r}"
        )

    def test_child_cancelled_json_returns_none(self) -> None:

        signal = _classify_generic_child_signal('{"type":"child_cancelled","child_id":"abc"}')
        assert signal is None, (
            f"child_cancelled MUST classify to None (terminal event), got {signal!r}"
        )

    def test_yaml_prose_line_returns_none(self) -> None:

        signal = _classify_generic_child_signal("User wrote a YAML snippet: subagent: worker")
        assert signal is None, (
            f"YAML prose line MUST classify to None (no leading"
            f" child-status marker), got {signal!r}"
        )

    def test_documentation_prose_line_returns_none(self) -> None:

        signal = _classify_generic_child_signal("Documentation says child: value")
        assert signal is None, (
            f"Documentation prose line MUST classify to None (no leading"
            f" child-status marker), got {signal!r}"
        )

    def test_subagent_colon_after_phrase_returns_none(self) -> None:

        signal = _classify_generic_child_signal("Let me talk about the subagent: overview of work")
        assert signal is None, (
            f"Phrase-like prose MUST classify to None (no leading"
            f" child-status marker), got {signal!r}"
        )

    def test_explicit_child_marker_still_classifies(self) -> None:

        signal = _classify_generic_child_signal("[child] do something")
        assert signal is not None, "Explicit child marker MUST still classify as CHILD_PROGRESS"
        assert signal.kind == AgentActivityKind.CHILD_PROGRESS

    def test_subagent_progress_json_still_classifies(self) -> None:

        signal = _classify_generic_child_signal('{"type":"subagent_progress","data":"thinking"}')
        assert signal is not None, (
            "Explicit subagent_progress JSON MUST still classify as CHILD_PROGRESS"
        )
        assert signal.kind == AgentActivityKind.CHILD_PROGRESS

    def test_subagent_heartbeat_json_still_classifies(self) -> None:

        signal = _classify_generic_child_signal('{"type":"subagent_heartbeat","ts":1234567890}')
        assert signal is not None, "Explicit subagent_heartbeat JSON MUST still classify"
        assert signal.kind == AgentActivityKind.CHILD_HEARTBEAT


class TestObserveLineDoesNotInvokeSinkForTerminalOrProse:
    def test_observe_line_does_not_invoke_sink_for_subagent_failed(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = BaseExecutionStrategy()
        strategy.observe_line('{"type":"subagent_failed","child_id":"abc"}')

        assert len(subagent_sink_spy) == 0, (
            f"terminal subagent_failed MUST NOT invoke the sink,"
            f" got {len(subagent_sink_spy)} invocations"
        )

    def test_observe_line_does_not_invoke_sink_for_subagent_cancelled(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = BaseExecutionStrategy()
        strategy.observe_line('{"event":"subagent_cancelled","child_id":"abc"}')

        assert len(subagent_sink_spy) == 0, (
            f"terminal subagent_cancelled MUST NOT invoke the sink,"
            f" got {len(subagent_sink_spy)} invocations"
        )

    def test_observe_line_does_not_invoke_sink_for_yaml_prose(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = BaseExecutionStrategy()
        strategy.observe_line("User wrote a YAML snippet: subagent: worker")

        assert len(subagent_sink_spy) == 0, (
            f"YAML prose MUST NOT invoke the sink (no leading"
            f" child-status marker), got {len(subagent_sink_spy)} invocations"
        )

    def test_observe_line_does_not_invoke_sink_for_documentation_prose(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = BaseExecutionStrategy()
        strategy.observe_line("Documentation says child: value")

        assert len(subagent_sink_spy) == 0, (
            f"Documentation prose MUST NOT invoke the sink (no leading"
            f" child-status marker), got {len(subagent_sink_spy)} invocations"
        )

    def test_observe_line_still_invokes_sink_for_explicit_child_marker(
        self, subagent_sink_spy: list[str]
    ) -> None:

        strategy = BaseExecutionStrategy()
        strategy.observe_line("[child] do something")

        assert len(subagent_sink_spy) == 1, (
            f"Explicit child marker MUST still invoke the sink,"
            f" got {len(subagent_sink_spy)} invocations"
        )


class TestBaseExecutionStrategyObserveLineWiring:
    def test_base_execution_strategy_observe_line_invokes_subagent_sink_on_child_signal(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = BaseExecutionStrategy()
        strategy.observe_line('{"type":"child_progress","msg":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"expected exactly one sink invocation for child_progress line,"
            f" got {len(subagent_sink_spy)} invocations"
        )
        assert subagent_sink_spy[0] == '{"type":"child_progress","msg":"thinking"}'

    def test_base_execution_strategy_observe_line_does_not_invoke_subagent_sink_on_blank_line(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = BaseExecutionStrategy()
        strategy.observe_line("   ")

        assert len(subagent_sink_spy) == 0, (
            f"expected zero sink invocations for blank line,"
            f" got {len(subagent_sink_spy)} invocations"
        )

    def test_base_execution_strategy_observe_line_does_not_invoke_sink_on_bare_heartbeat(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = BaseExecutionStrategy()
        strategy.observe_line('{"type":"heartbeat","ts":1234567890}')

        assert len(subagent_sink_spy) == 0, (
            f"expected zero sink invocations for bare heartbeat line"
            f" per analysis-feedback fix, got {len(subagent_sink_spy)}"
        )

    def test_base_execution_strategy_observe_line_does_not_invoke_sink_on_bare_tool_call(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = BaseExecutionStrategy()
        strategy.observe_line('{"type":"tool_call","name":"bash","args":{"cmd":"ls"}}')

        assert len(subagent_sink_spy) == 0, (
            f"expected zero sink invocations for bare tool_call line"
            f" per analysis-feedback fix, got {len(subagent_sink_spy)}"
        )

    def test_base_execution_strategy_observe_line_does_not_invoke_sink_on_bare_alive(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = BaseExecutionStrategy()
        strategy.observe_line('{"event":"alive","ts":1234567890}')

        assert len(subagent_sink_spy) == 0, (
            f"expected zero sink invocations for bare alive line"
            f" per analysis-feedback fix, got {len(subagent_sink_spy)}"
        )

    def test_base_execution_strategy_observe_line_does_not_invoke_sink_on_bare_progress(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = BaseExecutionStrategy()
        strategy.observe_line('{"event":"progress","data":"thinking"}')

        assert len(subagent_sink_spy) == 0, (
            f"expected zero sink invocations for bare progress line"
            f" per analysis-feedback fix, got {len(subagent_sink_spy)}"
        )

    def test_base_execution_strategy_observe_line_does_not_invoke_sink_on_bare_task_progress(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = BaseExecutionStrategy()
        strategy.observe_line('{"event":"task_progress","data":"thinking"}')

        assert len(subagent_sink_spy) == 0, (
            f"expected zero sink invocations for bare task_progress line"
            f" per analysis-feedback fix, got {len(subagent_sink_spy)}"
        )

    def test_base_execution_strategy_observe_line_invoke_sink_on_heartbeat(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = BaseExecutionStrategy()
        strategy.observe_line("Subagent heartbeat at t=42s")

        assert len(subagent_sink_spy) == 1
        assert subagent_sink_spy[0] == "Subagent heartbeat at t=42s"


class TestStrategyInheritanceUsesBaseChildSignalPath:
    def test_claude_execution_strategy_does_not_override_observe_line(self) -> None:
        assert "observe_line" not in ClaudeExecutionStrategy.__dict__, (
            "ClaudeExecutionStrategy MUST NOT override observe_line; the base"
            " implementation applies the cross-transport generic child-signal"
            " classifier automatically."
        )

    def test_claude_interactive_execution_strategy_does_not_override_observe_line(
        self,
    ) -> None:
        assert "observe_line" not in ClaudeInteractiveExecutionStrategy.__dict__, (
            "ClaudeInteractiveExecutionStrategy MUST NOT override observe_line; the"
            " base implementation applies the cross-transport generic child-signal"
            " classifier automatically."
        )

    def test_claude_execution_strategy_inherits_generic_child_signal_path(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = ClaudeExecutionStrategy()
        strategy.observe_line('{"type":"child_progress","msg":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"expected exactly one sink invocation for Claude strategy on"
            f" child_progress line, got {len(subagent_sink_spy)} invocations"
        )

    def test_claude_interactive_execution_strategy_inherits_generic_child_signal_path(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = ClaudeInteractiveExecutionStrategy()
        strategy.observe_line('{"type":"child_progress","msg":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"expected exactly one sink invocation for ClaudeInteractive strategy"
            f" on child_progress line, got {len(subagent_sink_spy)} invocations"
        )

    def test_generic_execution_strategy_does_not_override_observe_line(self) -> None:
        assert "observe_line" not in GenericExecutionStrategy.__dict__, (
            "GenericExecutionStrategy MUST NOT override observe_line; the base"
            " implementation applies the cross-transport generic child-signal"
            " classifier automatically. Codex / Nanocoder / Agy-fallback"
            " all inherit from GenericExecutionStrategy."
        )

    def test_generic_execution_strategy_inherits_generic_child_signal_path(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = GenericExecutionStrategy()
        strategy.observe_line('{"type":"child_progress","msg":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"expected exactly one sink invocation for Generic strategy on"
            f" child_progress line, got {len(subagent_sink_spy)} invocations"
        )

    def test_agy_strategy_inherits_base_observe_line(self) -> None:
        agy_instance = _make_agy_strategy()
        agy_cls = type(agy_instance)
        assert "observe_line" not in agy_cls.__dict__, (
            f"AgyExecutionStrategy ({agy_cls.__name__}) MUST NOT override"
            f" observe_line; the base implementation applies the cross-transport"
            f" generic child-signal classifier automatically."
        )
        assert issubclass(agy_cls, GenericExecutionStrategy), (
            f"AgyExecutionStrategy ({agy_cls.__name__}) MUST inherit from"
            f" GenericExecutionStrategy; got MRO: {agy_cls.__mro__}"
        )

    def test_agy_strategy_inherits_generic_child_signal_path(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = _make_agy_strategy()
        strategy.observe_line('{"type":"child_progress","msg":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"expected exactly one sink invocation for Agy strategy on"
            f" child_progress line, got {len(subagent_sink_spy)} invocations"
        )

    def test_codex_strategy_factory_returns_base_observer(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = strategy_for_transport(AgentTransport.CODEX)
        assert isinstance(strategy, GenericExecutionStrategy), (
            f"Codex transport must use GenericExecutionStrategy (the catalog"
            f" default), got {type(strategy).__name__}"
        )
        strategy.observe_line('{"type":"child_progress","msg":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"expected exactly one sink invocation for Codex strategy on"
            f" child_progress line, got {len(subagent_sink_spy)} invocations"
        )

    def test_codex_strategy_factory_does_not_invoke_sink_on_bare_event_name(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = strategy_for_transport(AgentTransport.CODEX)
        strategy.observe_line('{"event":"progress","data":"thinking"}')

        assert len(subagent_sink_spy) == 0, (
            f"bare Codex event=progress MUST NOT invoke the sink"
            f" per analysis-feedback fix (no-false-positive deferral),"
            f" got {len(subagent_sink_spy)} invocations"
        )

    def test_codex_strategy_factory_invokes_sink_on_child_scoped_event(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = strategy_for_transport(AgentTransport.CODEX)
        strategy.observe_line('{"event":"child_progress","data":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"Codex strategy MUST invoke the sink for child-scoped"
            f" event=child_progress line, got {len(subagent_sink_spy)} invocations"
        )

    def test_codex_strategy_factory_does_not_invoke_sink_on_bare_heartbeat(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = strategy_for_transport(AgentTransport.CODEX)
        strategy.observe_line('{"type":"heartbeat","ts":1234567890}')

        assert len(subagent_sink_spy) == 0, (
            f"bare Codex type=heartbeat MUST NOT invoke the sink"
            f" per analysis-feedback fix (no-false-positive deferral),"
            f" got {len(subagent_sink_spy)} invocations"
        )

    def test_codex_strategy_factory_does_not_invoke_sink_on_bare_tool_call(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = strategy_for_transport(AgentTransport.CODEX)
        strategy.observe_line('{"type":"tool_call","name":"bash"}')

        assert len(subagent_sink_spy) == 0, (
            f"bare Codex type=tool_call MUST NOT invoke the sink"
            f" per analysis-feedback fix (no-false-positive deferral),"
            f" got {len(subagent_sink_spy)} invocations"
        )

    def test_nanocoder_strategy_factory_returns_base_observer(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = strategy_for_transport(AgentTransport.NANOCODER)
        assert isinstance(strategy, GenericExecutionStrategy), (
            f"Nanocoder transport must use GenericExecutionStrategy (the catalog"
            f" default), got {type(strategy).__name__}"
        )
        strategy.observe_line('{"type":"child_progress","msg":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"expected exactly one sink invocation for Nanocoder strategy on"
            f" child_progress line, got {len(subagent_sink_spy)} invocations"
        )

    def test_agy_strategy_factory_returns_base_observer(self, subagent_sink_spy: list[str]) -> None:
        strategy = strategy_for_transport(AgentTransport.AGY)
        strategy.observe_line('{"type":"child_progress","msg":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"expected exactly one sink invocation for Agy strategy on"
            f" child_progress line, got {len(subagent_sink_spy)} invocations"
        )

    def test_agy_strategy_classifies_tool_activity(self) -> None:
        strategy = strategy_for_command("agy", AgentTransport.AGY)
        active_line = '{"event":"step_update","step_update":{"state":"ACTIVE","step_type":"tool","step_index":1,"tool_info":{"name":"createTodoList"}}}'
        done_line = '{"event":"step_update","step_update":{"state":"DONE","step_type":"tool","step_index":1,"tool_info":{"name":"createTodoList","output":"File created"}}}'

        active_signal = strategy.classify_activity_line(active_line)
        done_signal = strategy.classify_activity_line(done_line)

        assert active_signal is not None
        assert active_signal.kind == AgentActivityKind.TOOL_USE

        assert done_signal is not None
        assert done_signal.kind == AgentActivityKind.TOOL_RESULT


class TestOpenCodeStrategyDoesNotDoubleInvokeSink:
    def test_opencode_strategy_overrides_observe_line(self) -> None:
        assert "observe_line" in OpenCodeExecutionStrategy.__dict__, (
            "OpenCodeExecutionStrategy MUST override observe_line so the base"
            " generic classifier is NOT also called (would double-invoke the sink)."
        )

    def test_opencode_strategy_does_not_double_invoke_sink(
        self, subagent_sink_spy: list[str]
    ) -> None:
        strategy = OpenCodeExecutionStrategy()
        strategy.observe_line('{"type":"child_progress","child_id":"child-A","msg":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"expected exactly one sink invocation for OpenCode strategy on"
            f" child_progress line (NOT two -- base + override),"
            f" got {len(subagent_sink_spy)} invocations"
        )
