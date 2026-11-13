from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers.content_channel import ContentChannel
from ralph.agents.parsers.opencode import OpenCodeParser

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _line(obj: dict[str, object]) -> str:
    return json.dumps(obj)


class TestOpenCodeParserChannels:
    def test_running_tool_use_is_tool_call(self) -> None:
        raw = _line(
            {
                "type": "tool_use",
                "part": {"tool": "bash", "state": {"status": "running"}},
            }
        )
        results = list(OpenCodeParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_completed_tool_state_is_tool_call_then_tool_result(self) -> None:
        raw = _line(
            {
                "type": "tool_use",
                "part": {"tool": "bash", "state": {"status": "completed", "output": "ok"}},
            }
        )
        results = list(OpenCodeParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL),
            ("tool_result", ContentChannel.TOOL_RESULT),
        ]

    def test_errored_tool_state_is_tool_result_not_provider(self) -> None:
        raw = _line(
            {
                "type": "tool_use",
                "part": {"tool": "bash", "state": {"status": "error", "error": "boom"}},
            }
        )
        results = list(OpenCodeParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL),
            ("error", ContentChannel.TOOL_RESULT),
        ]

    def test_error_event_is_provider(self) -> None:
        raw = _line({"type": "error", "error": "quota exhausted"})
        results = list(OpenCodeParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.PROVIDER)
        ]
