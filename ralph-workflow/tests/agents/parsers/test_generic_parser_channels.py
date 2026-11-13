from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.agents.parsers.content_channel import ContentChannel
from ralph.agents.parsers.generic import GenericParser

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _line(obj: dict[str, object]) -> str:
    return json.dumps(obj)


class TestGenericParserChannels:
    def test_plain_tool_line_is_tool_call(self) -> None:
        results = list(GenericParser().parse(_lines("[plain] tool: bash")))
        assert [line.channel for line in results] == [ContentChannel.TOOL_CALL]

    def test_thinking_event_is_reasoning(self) -> None:
        results = list(GenericParser().parse(_lines(_line({"type": "message", "thought": "why"}))))
        assert [line.channel for line in results] == [ContentChannel.REASONING]

    @pytest.mark.parametrize(
        "raw",
        [
            _line({"type": "error", "error": "boom"}),
            "just a status update",
        ],
    )
    def test_error_and_unparsed_lines_are_provider(self, raw: str) -> None:
        results = list(GenericParser().parse(_lines(raw)))
        assert [line.channel for line in results] == [ContentChannel.PROVIDER]
