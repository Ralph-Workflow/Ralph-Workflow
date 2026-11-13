from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers.claude_interactive import ClaudeInteractiveParser
from ralph.agents.parsers.content_channel import ContentChannel

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _line(obj: dict[str, object]) -> str:
    return json.dumps(obj)


class TestClaudeInteractiveParserChannels:
    def test_tool_use_event_is_tool_call(self) -> None:
        raw = _line(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "t1", "name": "bash", "input": {}}]
                },
            }
        )
        results = list(ClaudeInteractiveParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_errored_tool_result_is_tool_result_not_provider(self) -> None:
        parser = ClaudeInteractiveParser()
        tool_use = _line(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "t1", "name": "bash", "input": {}}]
                },
            }
        )
        tool_result = _line(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "is_error": True,
                            "content": "RESOURCE_EXHAUSTED (code 429)",
                        }
                    ]
                },
            }
        )
        results = list(parser.parse(_lines(tool_use, tool_result)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL),
            ("error", ContentChannel.TOOL_RESULT),
        ]

    def test_provider_failure_text_is_provider(self) -> None:
        results = list(ClaudeInteractiveParser().parse(_lines("500 internal server error: boom\n")))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.PROVIDER)
        ]

    def test_thinking_event_is_reasoning(self) -> None:
        parser = ClaudeInteractiveParser()
        raw = _line(
            {
                "type": "assistant",
                "message": {"content": [{"type": "thinking", "thinking": "plan"}]},
            }
        )
        results = list(parser.parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("thinking", ContentChannel.REASONING)
        ]
