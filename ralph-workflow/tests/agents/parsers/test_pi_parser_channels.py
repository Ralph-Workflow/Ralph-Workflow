from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers.content_channel import ContentChannel
from ralph.agents.parsers.pi import PiParser

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _line(obj: dict[str, object]) -> str:
    return json.dumps(obj)


class TestPiParserChannels:
    def test_tool_execution_start_is_tool_call(self) -> None:
        raw = _line({"type": "tool_execution_start", "toolName": "bash", "args": {}})
        results = list(PiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_errored_tool_execution_end_is_tool_result_not_provider(self) -> None:
        raw = _line(
            {
                "type": "tool_execution_end",
                "toolName": "bash",
                "isError": True,
                "result": "RESOURCE_EXHAUSTED (code 429)",
            }
        )
        results = list(PiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.TOOL_RESULT)
        ]

    def test_tool_result_block_is_tool_result(self) -> None:
        raw = _line(
            {
                "type": "message_end",
                "message": {
                    "content": [
                        {
                            "type": "toolResult",
                            "result": {"content": [{"type": "text", "text": "ok"}]},
                        }
                    ]
                },
            }
        )
        results = list(PiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_result", ContentChannel.TOOL_RESULT)
        ]

    def test_thinking_end_is_reasoning(self) -> None:
        raw = _line(
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "thinking_end",
                    "content": "plan",
                    "contentIndex": 0,
                },
            }
        )
        results = list(PiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("thinking", ContentChannel.REASONING)
        ]

    def test_extension_error_is_provider(self) -> None:
        raw = _line({"type": "extension_error", "error": "quota exhausted"})
        results = list(PiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.PROVIDER)
        ]
