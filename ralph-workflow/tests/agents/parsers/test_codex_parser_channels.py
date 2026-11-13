from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers.codex import CodexParser
from ralph.agents.parsers.content_channel import ContentChannel

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _line(obj: dict[str, object]) -> str:
    return json.dumps(obj)


class TestCodexParserChannels:
    def test_reasoning_item_is_reasoning(self) -> None:
        raw = _line({"type": "item.completed", "item": {"type": "reasoning", "text": "hmm"}})
        results = list(CodexParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("thinking", ContentChannel.REASONING)
        ]

    def test_completed_tool_call_item_is_tool_result(self) -> None:
        raw = _line(
            {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "tool": "read_multiple_files",
                    "result": {"content": [{"type": "text", "text": "ok"}]},
                },
            }
        )
        results = list(CodexParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_result", ContentChannel.TOOL_RESULT)
        ]

    def test_tool_use_item_is_tool_call(self) -> None:
        raw = _line(
            {
                "type": "item.started",
                "item": {
                    "type": "command_execution",
                    "command": "echo hi",
                    "id": "c1",
                },
            }
        )
        results = list(CodexParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_error_event_is_provider(self) -> None:
        raw = _line({"type": "error", "message": "RESOURCE_EXHAUSTED (code 429)"})
        results = list(CodexParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.PROVIDER)
        ]
