from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.activity import AgentActivityKind, AgentActivitySignal
from ralph.agents.parsers.claude_interactive import ClaudeInteractiveTranscriptParser

from ._helpers import _check_signals_terminal
from .agent_execution_state import AgentExecutionState
from .claude_execution_strategy import ClaudeExecutionStrategy

if TYPE_CHECKING:
    from ralph.agents.completion_signals import CompletionSignals
    from ralph.process.liveness import LivenessProbe

    from ._live_descendant_handle import _LiveDescendantHandle


class ClaudeInteractiveExecutionStrategy(ClaudeExecutionStrategy):
    """Interactive Claude session strategy.

    Uses a VT-aware transcript parser before falling back to the headless Claude
    classifier so TUI repaint noise does not downgrade meaningful tool/lifecycle
    lines into generic output.
    """

    def __init__(self) -> None:
        self._transcript_parser = ClaudeInteractiveTranscriptParser()

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        events = self._transcript_parser.feed(line)
        if events:
            event = events[-1]
            if event.kind == "tool_use":
                return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=event.text)
            if event.kind == "lifecycle":
                return AgentActivitySignal(AgentActivityKind.LIFECYCLE, raw=event.text)
            if event.kind == "session":
                return AgentActivitySignal(AgentActivityKind.LIFECYCLE, raw=event.text)
            return AgentActivitySignal(AgentActivityKind.OUTPUT_LINE, raw=event.text)
        return super().classify_activity_line(line)

    def supports_session_continuation(self) -> bool:
        return True

    def classify_exit(
        self,
        handle: _LiveDescendantHandle,
        completion_signals: CompletionSignals,
        liveness_probe: LivenessProbe | None = None,
    ) -> AgentExecutionState:
        del handle, liveness_probe
        if _check_signals_terminal(completion_signals):
            return AgentExecutionState.TERMINAL_COMPLETE
        return AgentExecutionState.RESUMABLE_CONTINUE
