"""Semantic parsing for PTY-backed Claude interactive transcript lines."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ralph.agents.parsers.base import AgentOutputLine, TextAccumulator
from ralph.display.vt_normalizer import normalize_vt_text

if TYPE_CHECKING:
    from collections.abc import Iterator

_SESSION_ID_PATTERNS = (
    re.compile(r"session\s+id\s*[:=]\s*([A-Za-z0-9._:-]+)", re.IGNORECASE),
    re.compile(r"--resume\s+([A-Za-z0-9._:-]+)"),
)


@dataclass(frozen=True)
class InteractiveTranscriptEvent:
    """Semantic event extracted from the interactive Claude transcript surface."""

    kind: str
    text: str


def _extract_message_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


class ClaudeInteractiveTranscriptParser:
    """Extract semantic events from a normalized Claude interactive transcript."""

    def __init__(self) -> None:
        self.session_id: str | None = None
        self._last_emitted_text: str | None = None

    def feed(self, raw_text: str) -> list[InteractiveTranscriptEvent]:
        json_events = self._events_from_json(raw_text)
        if json_events:
            return json_events
        normalized = normalize_vt_text(raw_text)
        events: list[InteractiveTranscriptEvent] = []
        for line in normalized.splitlines():
            text = line.strip()
            if not text or text == self._last_emitted_text:
                continue
            if not any(character.isalnum() for character in text):
                continue
            event = self._event_for_text(text)
            if event is not None:
                events.append(event)
                self._last_emitted_text = text
        return events

    def _events_from_json(self, raw_text: str) -> list[InteractiveTranscriptEvent]:  # noqa: PLR0912
        try:
            parsed = cast("object", json.loads(raw_text))
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, dict):
            return []
        obj = cast("dict[str, object]", parsed)
        event_type = str(obj.get("type", ""))
        events: list[InteractiveTranscriptEvent] = []
        session_id = obj.get("sessionId") or obj.get("session_id")
        if isinstance(session_id, str) and session_id:
            self.session_id = session_id
            events.append(InteractiveTranscriptEvent(kind="session", text=session_id))
        if event_type == "assistant":
            message = obj.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, list):
                    for item in content:
                        if not isinstance(item, dict):
                            continue
                        item_type = str(item.get("type", ""))
                        if item_type == "tool_use":
                            tool_name = str(item.get("name", "tool"))
                            events.append(
                                InteractiveTranscriptEvent(
                                    kind="tool_use", text=f"claude tool: {tool_name}"
                                )
                            )
                        elif item_type == "text":
                            text = str(item.get("text", "")).strip()
                            if text:
                                events.append(InteractiveTranscriptEvent(kind="output", text=text))
                        elif item_type == "thinking":
                            text = str(item.get("thinking", "")).strip()
                            if text:
                                events.append(
                                    InteractiveTranscriptEvent(kind="thinking", text=text)
                                )
        elif event_type == "user":
            message = obj.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, list):
                    for item in content:
                        if not isinstance(item, dict):
                            continue
                        user_item_type: object = item.get("type")
                        if user_item_type != "tool_result":
                            continue
                        text = _extract_message_text(item.get("content")).strip()
                        if text:
                            events.append(
                                InteractiveTranscriptEvent(
                                    kind="tool_result", text=f"claude result: {text}"
                                )
                            )
        return [event for event in events if event.text != self._last_emitted_text]

    def _event_for_text(self, text: str) -> InteractiveTranscriptEvent | None:
        for pattern in _SESSION_ID_PATTERNS:
            match = pattern.search(text)
            if match is not None:
                self.session_id = match.group(1)
                return InteractiveTranscriptEvent(kind="session", text=text)
        if text.startswith("claude tool:") or " tool: " in text:
            return InteractiveTranscriptEvent(kind="tool_use", text=text)
        if text.startswith("claude result:"):
            return InteractiveTranscriptEvent(kind="tool_result", text=text)
        if text.startswith("[claude]:") or text.startswith("claude ") or text.startswith("claude/"):
            return InteractiveTranscriptEvent(kind="lifecycle", text=text)
        return InteractiveTranscriptEvent(kind="output", text=text)


class ClaudeInteractiveParser:
    """Convert interactive Claude transcript lines into AgentOutputLine events."""

    def __init__(self) -> None:
        self._parser = ClaudeInteractiveTranscriptParser()
        self._text_accumulator = TextAccumulator()
        self._thinking_accumulator = TextAccumulator()

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        for raw in lines:
            for event in self._parser.feed(raw):
                if event.kind == "output":
                    self._text_accumulator.buffer += event.text + "\n"
                    self._text_accumulator.raw_lines.append(raw)
                    continue
                if event.kind == "thinking":
                    self._thinking_accumulator.buffer += event.text + "\n"
                    self._thinking_accumulator.raw_lines.append(raw)
                    continue
                yield from self._flush_accumulators()
                if event.kind == "session":
                    continue
                elif event.kind == "tool_use":
                    tool_name = (
                        event.text.split(":", 1)[-1].strip()
                        if ":" in event.text
                        else event.text
                    )
                    yield AgentOutputLine(
                        type="tool_use",
                        content=tool_name,
                        raw=raw,
                        metadata={"tool": tool_name},
                    )
                elif event.kind == "tool_result":
                    yield AgentOutputLine(type="tool_result", content=event.text, raw=raw)
                elif event.kind == "lifecycle":
                    continue
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
