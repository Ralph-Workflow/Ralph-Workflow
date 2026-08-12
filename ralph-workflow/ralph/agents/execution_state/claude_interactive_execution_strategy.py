"""Execution strategy for interactive Claude agents."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.activity import AgentActivityKind, AgentActivitySignal
from ralph.agents.parsers.claude_interactive import ClaudeInteractiveTranscriptParser

from .agent_execution_state import AgentExecutionState
from .claude_execution_strategy import ClaudeExecutionStrategy

if TYPE_CHECKING:
    from ralph.agents.parsers.interactive_transcript_event import InteractiveTranscriptEvent
    from ralph.process.liveness import LivenessProbe

    from ._live_descendant_handle import LiveDescendantHandle


def _interactive_tool_use_raw(event: InteractiveTranscriptEvent) -> str:
    """Return a watchdog-readable raw payload for an interactive tool_use event.

    ``event.text`` is the operator-facing ``claude tool: <name>`` marker and
    stays exactly that -- the display and several parsers key off it. But the
    marker carries NO arguments, so feeding it to the tool-call circuit breaker
    fingerprinted every invocation of one tool as ``<name>|{}``: ten different
    ``Bash`` commands looked like one command repeated ten times and fired
    REPEATED_IDENTICAL_TOOL_CALL on a healthy agent at the seventh call. This
    is the default ``claude`` agent, so that false kill was the common case.

    The transcript parser already extracts ``input`` into ``event.metadata``
    (see ``_TranscriptItemHandler``); this re-packs name + input into the
    canonical ``{"type": "tool_use", ...}`` envelope the extractor understands.
    Falls back to the plain marker when the event carries no tool metadata, so
    the extractor's plain-text branch still applies.
    """
    metadata = event.metadata or {}
    tool_name = metadata.get("tool")
    if not isinstance(tool_name, str) or not tool_name:
        return event.text
    tool_input = metadata.get("input")
    payload: dict[str, object] = {
        "type": "tool_use",
        "name": tool_name,
        "input": tool_input if isinstance(tool_input, dict) else {},
    }
    return json.dumps(payload, sort_keys=True, default=str)


class ClaudeInteractiveExecutionStrategy(ClaudeExecutionStrategy):
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
                    return AgentActivitySignal(
                        AgentActivityKind.TOOL_USE,
                        raw=_interactive_tool_use_raw(event),
                    )
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
        handle: LiveDescendantHandle,
        liveness_probe: LivenessProbe,
    ) -> AgentExecutionState:
        del handle, liveness_probe
        return AgentExecutionState.ACTIVE

    def supports_session_continuation(self) -> bool:
        return True
