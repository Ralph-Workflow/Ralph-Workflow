from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers.content_channel import ContentChannel
from ralph.agents.parsers.cursor import CursorParser

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _line(obj: dict[str, object]) -> str:
    return json.dumps(obj)


class TestCursorParserChannels:
    def test_tool_call_event_is_tool_call(self) -> None:
        raw = _line({"type": "tool_call", "toolName": "bash", "args": {}})
        results = list(CursorParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_completed_tool_call_event_is_tool_result(self) -> None:
        raw = _line(
            {
                "type": "tool_call",
                "subtype": "completed",
                "toolName": "bash",
                "result": {"content": "ok"},
            }
        )
        results = list(CursorParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_result", ContentChannel.TOOL_RESULT)
        ]

    def test_errored_tool_result_is_tool_result_not_provider(self) -> None:
        raw = _line({"type": "tool_result", "error": "RESOURCE_EXHAUSTED (code 429)"})
        results = list(CursorParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.TOOL_RESULT)
        ]

    def test_thinking_event_is_reasoning(self) -> None:
        parser = CursorParser()
        results = list(parser.parse(_lines(_line({"type": "thinking", "text": "hmm"}))))
        results.extend(parser.flush_accumulators())
        assert [(line.type, line.channel) for line in results] == [
            ("thinking", ContentChannel.REASONING)
        ]
