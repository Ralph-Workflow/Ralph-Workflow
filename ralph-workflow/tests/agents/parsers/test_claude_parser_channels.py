from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers.claude import ClaudeParser
from ralph.agents.parsers.content_channel import ContentChannel

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _line(obj: dict[str, object]) -> str:
    return json.dumps(obj)


class TestClaudeParserChannels:
    def test_tool_use_block_is_tool_call(self) -> None:
        raw = _line(
            {
                "type": "assistant",
                "message": {
                    "id": "m1",
                    "content": [{"type": "tool_use", "id": "t1", "name": "bash", "input": {}}],
                },
            }
        )
        results = list(ClaudeParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_errored_tool_result_block_is_tool_result_not_provider(self) -> None:
        raw = _line(
            {
                "type": "user",
                "message": {
                    "id": "m2",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "is_error": True,
                            "content": "RESOURCE_EXHAUSTED (code 429)",
                        }
                    ],
                },
            }
        )
        results = list(ClaudeParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.TOOL_RESULT)
        ]

    def test_thinking_block_is_reasoning(self) -> None:
        raw = _line(
            {
                "type": "assistant",
                "message": {
                    "id": "m3",
                    "content": [{"type": "thinking", "thinking": "planning"}],
                },
            }
        )
        results = list(ClaudeParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("thinking", ContentChannel.REASONING)
        ]

    def test_error_event_is_provider(self) -> None:
        raw = _line({"type": "error", "error": {"message": "quota exhausted"}})
        results = list(ClaudeParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.PROVIDER)
        ]
