from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.parsers.content_channel import ContentChannel
from ralph.agents.parsers.nanocoder import NanocoderParser

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


class TestNanocoderParserChannels:
    def test_executed_tool_line_is_tool_call(self) -> None:
        results = list(NanocoderParser().parse(_lines("⚒ Executed Read " + chr(215) + " 2")))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_plain_tool_line_is_tool_call(self) -> None:
        results = list(NanocoderParser().parse(_lines("[plain] tool: bash")))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_text_line_is_provider(self) -> None:
        results = list(NanocoderParser().parse(_lines("hello world this is output")))
        assert [(line.type, line.channel) for line in results] == [
            ("text", ContentChannel.PROVIDER)
        ]
