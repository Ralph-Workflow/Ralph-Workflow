"""Convert interactive Claude transcript lines into agent parser output."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._event_classification import is_lifecycle_kind
from .agent_output_line import AgentOutputLine
from .claude_interactive_transcript_parser import ClaudeInteractiveTranscriptParser
from .interactive_transcript_event import InteractiveTranscriptEvent
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Iterator


class ClaudeInteractiveParser:
    """Convert interactive Claude transcript lines into AgentOutputLine events."""

    def __init__(self) -> None:
        self._parser = ClaudeInteractiveTranscriptParser()
        self._text_accumulator = TextAccumulator()
        self._thinking_accumulator = TextAccumulator()

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
                    yield AgentOutputLine(
                        type="tool_use",
                        content=tool_name,
                        raw=raw,
                        metadata={"tool": tool_name},
                    )
                    continue
                if event.kind == "tool_result":
                    yield AgentOutputLine(type="tool_result", content=event.text, raw=raw)
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
