from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers.content_channel import ContentChannel
from ralph.agents.parsers.kimi import KimiParser

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _line(obj: dict[str, object]) -> str:
    return json.dumps(obj)


class TestKimiParserChannels:
    def test_assistant_tool_call_is_tool_call(self) -> None:
        raw = _line(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "Read", "arguments": "{}"}}],
            }
        )
        results = list(KimiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_tool_result_is_tool_result(self) -> None:
        raw = _line({"role": "tool", "tool_call_id": "c1", "content": "ok"})
        results = list(KimiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_result", ContentChannel.TOOL_RESULT)
        ]

    def test_errored_tool_payload_is_tool_result_not_provider(self) -> None:
        raw = _line(
            {
                "role": "tool",
                "tool_call_id": "c1",
                "is_error": True,
                "content": "RESOURCE_EXHAUSTED (code 429)",
            }
        )
        results = list(KimiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.TOOL_RESULT)
        ]

    def test_meta_event_is_provider(self) -> None:
        raw = _line({"role": "meta", "type": "system.version", "version": "1.0"})
        results = list(KimiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("lifecycle", ContentChannel.PROVIDER)
        ]
