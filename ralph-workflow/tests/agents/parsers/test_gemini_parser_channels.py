from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers.content_channel import ContentChannel
from ralph.agents.parsers.gemini import GeminiParser

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _line(obj: dict[str, object]) -> str:
    return json.dumps(obj)


class TestGeminiParserChannels:
    def test_tool_call_event_is_tool_call(self) -> None:
        raw = _line({"type": "tool_call", "name": "lookup", "args": {"q": "x"}})
        results = list(GeminiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_function_call_event_is_tool_result(self) -> None:
        raw = _line({"type": "function_call", "response": "done"})
        results = list(GeminiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_result", ContentChannel.TOOL_RESULT)
        ]

    def test_thought_part_is_reasoning(self) -> None:
        raw = _line({"type": "message", "parts": [{"text": "why", "thought": True}]})
        results = list(GeminiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("thinking", ContentChannel.REASONING)
        ]

    def test_error_event_is_provider(self) -> None:
        raw = _line({"type": "error", "message": "quota exhausted"})
        results = list(GeminiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.PROVIDER)
        ]
