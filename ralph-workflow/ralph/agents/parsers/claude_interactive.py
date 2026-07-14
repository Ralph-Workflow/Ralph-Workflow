"""Convert interactive Claude transcript lines into agent parser output."""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

from ._event_classification import is_lifecycle_kind
from ._template import (
    _EMITTABLE_TYPES,
    _MAX_PREFIX_LENGTH,
    _MAX_SUMMARY_LENGTH,
    _sanitize_parser_subagent_summary,
    _tool_name_from_line,
)
from .agent_output_line import AgentOutputLine
from .claude_interactive_transcript_parser import ClaudeInteractiveTranscriptParser
from .interactive_transcript_event import InteractiveTranscriptEvent
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry

_MAX_TRACKED_TOOL_USES = 128
_ToolMap = OrderedDict[str, str]


class ClaudeInteractiveParser:
    """Convert interactive Claude transcript lines into AgentOutputLine events."""

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        # R5: bind the per-invocation shared SubagentPidRegistry + per-transport
        # source label. Interactive Claude's transcript events do not
        # currently carry embedded PIDs; this is forward-compat for the
        # per-transport SubagentPidSource seam.
        self._subagent_pid_registry: SubagentPidRegistry | None = subagent_pid_registry
        self._subagent_source_label: str | None = subagent_source_label
        self._parser = ClaudeInteractiveTranscriptParser()
        self._text_accumulator = TextAccumulator()
        self._thinking_accumulator = TextAccumulator()
        self._last_tool_name: str | None = None
        self._tool_names_by_id: _ToolMap = OrderedDict()  # bounded-accumulator-ok: cap 128

    def emit_subagent_activity(
        self,
        line: AgentOutputLine,
        sink: Callable[[str], None],
    ) -> None:
        """Forward a parsed interactive-Claude line to the subagent sink.

        Parallel standalone implementation of the
        :meth:`ParserTemplateBase.emit_subagent_activity` hook for the
        interactive-Claude transport (which does NOT inherit from
        :class:`ParserTemplateBase`).  The hook fires ONLY for the
        four ``_EMITTABLE_TYPES`` kinds so a hard-failing child or a
        lifecycle heartbeat does not keep the watchdog deferring a
        kill.  Sink exceptions are swallowed so a buggy sink cannot
        crash the activity stream.

        Summary format matches the NDJSON parser layer: ``tool_use:<name>``
        for tool calls, ``tool_result:<name>`` for tool results,
        ``text:<first-80-chars>`` for model text, and
        ``thinking:<first-80-chars>`` for thinking blocks.
        """
        if line.type not in _EMITTABLE_TYPES:
            return
        tool_name = _tool_name_from_line(line)
        if line.type in {"tool_use", "tool_result"}:
            summary = f"{line.type}:{tool_name}"
        else:
            content = line.content.strip()
            truncated = (
                content[:_MAX_PREFIX_LENGTH] if len(content) > _MAX_PREFIX_LENGTH else content
            )
            summary = f"{line.type}:{truncated}"
        summary = _sanitize_parser_subagent_summary(summary)
        if not summary:
            return
        if len(summary) > _MAX_SUMMARY_LENGTH:
            summary = summary[:_MAX_SUMMARY_LENGTH]
        try:
            sink(summary)
        except Exception:
            return

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        for raw in lines:
            for event in self._parser.feed(raw):
                if is_lifecycle_kind(event.kind):
                    continue
                if event.kind == "output":
                    self._text_accumulator.buffer += event.text + "\n"
                    self._text_accumulator.raw_lines.append(raw)
                    continue
                if event.kind == "thinking":
                    self._thinking_accumulator.buffer += event.text + " "
                    self._thinking_accumulator.raw_lines.append(raw)
                    continue
                yield from self._flush_accumulators()
                if event.kind == "session":
                    continue
                if event.kind == "error":
                    yield AgentOutputLine(type="error", content=event.text, raw=raw)
                    continue
                if event.kind == "tool_use":
                    tool_name = (
                        event.text.split(":", 1)[-1].strip() if ":" in event.text else event.text
                    )
                    self._last_tool_name = tool_name
                    metadata = dict(event.metadata)
                    metadata["tool"] = tool_name
                    tool_use_id = metadata.get("tool_use_id")
                    if isinstance(tool_use_id, str) and tool_use_id:
                        self._tool_names_by_id[tool_use_id] = tool_name
                        self._tool_names_by_id.move_to_end(tool_use_id)
                        if len(self._tool_names_by_id) > _MAX_TRACKED_TOOL_USES:
                            self._tool_names_by_id.popitem(last=False)
                    yield AgentOutputLine(
                        type="tool_use",
                        content=tool_name,
                        raw=raw,
                        metadata=metadata,
                    )
                    continue
                if event.kind == "tool_result":
                    metadata = dict(event.metadata)
                    tool_use_id = metadata.get("tool_use_id")
                    if isinstance(tool_use_id, str) and tool_use_id:
                        tool_name = (
                            self._tool_names_by_id.pop(tool_use_id)
                            if tool_use_id in self._tool_names_by_id
                            else self._last_tool_name or "unknown"
                        )
                    else:
                        tool_name = self._last_tool_name or "unknown"
                    metadata["tool"] = tool_name
                    content = event.text.removeprefix("claude result:").lstrip()
                    yield AgentOutputLine(
                        type="tool_result",
                        content=content,
                        raw=raw,
                        metadata=metadata,
                    )
        yield from self._flush_accumulators()

    def _flush_accumulators(self) -> Iterator[AgentOutputLine]:
        text = self._text_accumulator.buffer.strip()
        if text:
            raw = "\n".join(self._text_accumulator.raw_lines)
            yield AgentOutputLine(type="text", content=text, raw=raw)
        self._text_accumulator.buffer = ""
        self._text_accumulator.raw_lines = []

        thinking = self._thinking_accumulator.buffer.strip()
        if thinking:
            raw = "\n".join(self._thinking_accumulator.raw_lines)
            yield AgentOutputLine(type="thinking", content=thinking, raw=raw)
        self._thinking_accumulator.buffer = ""
        self._thinking_accumulator.raw_lines = []


__all__ = [
    "ClaudeInteractiveParser",
    "ClaudeInteractiveTranscriptParser",
    "InteractiveTranscriptEvent",
]
