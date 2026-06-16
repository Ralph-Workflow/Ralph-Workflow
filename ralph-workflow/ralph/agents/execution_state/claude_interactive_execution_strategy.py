"""Execution strategy for interactive Claude agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.activity import AgentActivityKind, AgentActivitySignal
from ralph.agents.parsers.claude_interactive import ClaudeInteractiveTranscriptParser

from ._completion_mixin import CompletionEnforcingStrategy
from .agent_execution_state import AgentExecutionState
from .claude_execution_strategy import ClaudeExecutionStrategy

if TYPE_CHECKING:
    from ralph.process.liveness import LivenessProbe

    from ._live_descendant_handle import _LiveDescendantHandle


class ClaudeInteractiveExecutionStrategy(CompletionEnforcingStrategy, ClaudeExecutionStrategy):
    """Interactive Claude session strategy.

    Uses a VT-aware transcript parser before falling back to the headless Claude
    classifier so TUI repaint noise does not downgrade meaningful tool/lifecycle
    lines into generic output.
    """

    def __init__(self, **kwargs: object) -> None:
        del kwargs
        self._transcript_parser = ClaudeInteractiveTranscriptParser()

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        events = self._transcript_parser.feed(line)
        if events:
            tool_result_event = None
            lifecycle_event = None
            thinking_event = None
            output_event = None
            for event in events:
                if event.kind == "tool_use":
                    return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=event.text)
                if event.kind in {"lifecycle", "session"} and lifecycle_event is None:
                    lifecycle_event = event
                    continue
                if event.kind == "tool_result" and tool_result_event is None:
                    tool_result_event = event
                    continue
                if event.kind == "thinking" and thinking_event is None:
                    thinking_event = event
                    continue
                if output_event is None:
                    output_event = event
            if lifecycle_event is not None:
                return AgentActivitySignal(AgentActivityKind.LIFECYCLE, raw=lifecycle_event.text)
            if tool_result_event is not None:
                return AgentActivitySignal(
                    AgentActivityKind.TOOL_RESULT,
                    raw=tool_result_event.text,
                )
            priority_event = output_event or thinking_event
            if priority_event is not None:
                return AgentActivitySignal(AgentActivityKind.OUTPUT_LINE, raw=priority_event.text)
        if self._transcript_parser._current_content_mode == "thinking":
            return None
        return super().classify_activity_line(line)

    def classify_quiet(
        self,
        handle: _LiveDescendantHandle,
        liveness_probe: LivenessProbe,
    ) -> AgentExecutionState:
        del handle, liveness_probe
        return AgentExecutionState.ACTIVE

    def supports_session_continuation(self) -> bool:
        return True
